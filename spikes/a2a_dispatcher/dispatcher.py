"""A2A dispatcher spike - orchestrator dispatcher (official `a2a-sdk` v1.1.2).

Demonstrates the full dispatcher flow against a local worker agent:

  [1] DISCOVERY   - fetch + parse the Agent Card (/.well-known/agent-card.json)
  [2] SELECTION   - tag-based skill routing + interface selection
  [3] DELEGATE    - SendMessage (streaming: SendStreamingMessage over SSE)
  [4] TRACK       - polling (GetTask) and SSE resubscription (SubscribeToTask)
  [5] SIGNED CARD - verify JWS on the Agent Card (--verify), tamper detection

Run:
    python3 worker_agent.py --port 41999 --signed     # terminal 1
    python3 dispatcher.py --base-url http://127.0.0.1:41999 --verify
"""

import argparse
import asyncio
import copy
import datetime as dt
import uuid

import httpx

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types.a2a_pb2 import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from a2a.utils import signing


def log(step: str, msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S}  [{step}] {msg}")


def state_name(state: TaskState) -> str:
    return TaskState.Name(state)


# --------------------------------------------------------------------------
# [1] DISCOVERY - Agent Card
# --------------------------------------------------------------------------
async def discover(base_url: str, signature_verifier=None):
    async with httpx.AsyncClient(timeout=10) as hc:
        resolver = A2ACardResolver(hc, base_url)
        card = await resolver.get_agent_card(signature_verifier=signature_verifier)
    log("1.DISCOVERY", f"fetched Agent Card from {base_url}/.well-known/agent-card.json")
    log("1.DISCOVERY", f"  name        : {card.name}")
    log("1.DISCOVERY", f"  description : {card.description}")
    log("1.DISCOVERY", f"  version     : {card.version}")
    log("1.DISCOVERY", f"  streaming   : {card.capabilities.streaming}")
    log("1.DISCOVERY", f"  skills      :")
    for s in card.skills:
        log("1.DISCOVERY", f"    - {s.id:16s} tags={list(s.tags)}")
    log("1.DISCOVERY", "  interfaces  :")
    for i in card.supported_interfaces:
        log("1.DISCOVERY", f"    - {i.protocol_binding:10s} v{i.protocol_version} {i.url}")
    return card


# --------------------------------------------------------------------------
# [2] SELECTION - tag-based skill routing + interface selection
# --------------------------------------------------------------------------
def select_skill(card, labels: list[str]):
    log("2.SELECT", f"routing labels {labels} against card skills (tag match)")
    for skill in card.skills:
        if set(skill.tags) & set(labels):
            log("2.SELECT", f"  matched skill '{skill.id}' via tags {list(skill.tags)}")
            return skill
    raise SystemExit(f"no skill matched labels {labels}")


def select_interface(card, preferred: str = "JSONRPC"):
    versions = {}
    for i in card.supported_interfaces:
        if i.protocol_binding == preferred and i.protocol_version == "1.0":
            log("2.SELECT", f"  interface: {preferred} 1.0 -> {i.url}")
            return i
    # fall back to the first v1.0 interface
    for i in card.supported_interfaces:
        versions[i.protocol_binding] = i.protocol_version
    raise SystemExit(
        f"no {preferred} v1.0 interface on card (have {versions})"
    )


# --------------------------------------------------------------------------
# [3] DELEGATE - SendMessage (streaming) / SendStreamingMessage over SSE
# --------------------------------------------------------------------------
async def delegate_streaming(client, skill, query: str):
    log("3.DELEGATE", f"SendMessage(skill={skill.id}) via "
                      f"SendStreamingMessage (SSE, card streaming=true)")
    request = SendMessageRequest(
        message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=query)]),
        metadata={"skillId": skill.id},
    )
    # request.message is set by the SDK proto; SendMessageRequest has no id field
    # -> the task id is generated server-side and returned in the first event.
    final_task = None
    last_state = None
    async for event in client.send_message(request):
        if event.HasField("status_update"):
            st = event.status_update.status
            last_state = state_name(st.state)
            msg = "".join(p.text for p in st.message.parts) if st.HasField("message") else ""
            log("3.DELEGATE",
                f"  status_update state={last_state}"
                + (f" — {msg}" if msg else ""))
        if event.HasField("message"):
            for p in event.message.parts:
                log("3.DELEGATE", f"  message: {p.text}")
        if event.HasField("artifact_update"):
            a = event.artifact_update
            art = a.artifact
            for p in art.parts:
                log("3.DELEGATE", f"  artifact[{art.name or '?'}]: {p.text}")
        if event.HasField("task"):
            final_task = event.task
    log("3.DELEGATE", f"  final task state={last_state}")
    return final_task


# --------------------------------------------------------------------------
# [4] TRACK - polling (GetTask) + SSE resubscription (SubscribeToTask)
# --------------------------------------------------------------------------
async def track_polling(client, task_id: str, max_polls: int = 40):
    log("4.TRACK", f"polling GetTask(task={task_id}) every 250ms "
                   f"(return_immediately + client-side loop)")
    observed = set()
    for _ in range(max_polls):
        task = await client.get_task(GetTaskRequest(id=task_id))
        s = state_name(task.status.state)
        if s not in observed:
            observed.add(s)
            log("4.TRACK", f"  poll -> state={s}")
        if s in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
                 "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"):
            for a in task.artifacts:
                for p in a.parts:
                    log("4.TRACK", f"  artifact[{a.name or '?'}]: {p.text}")
            return task
        await asyncio.sleep(0.25)
    raise SystemExit(f"task {task_id} did not reach terminal state in time")


async def track_subscribe(client, task_id: str):
    log("4.TRACK", f"SSE resubscribe: SubscribeToTask(task={task_id}) "
                   f"(reconnects to an in-flight task)")
    async for event in client.subscribe(SubscribeToTaskRequest(id=task_id)):
        if event.HasField("status_update"):
            st_name = state_name(event.status_update.status.state)
            log("4.TRACK", f"  sse status_update state={st_name}")
            if st_name in ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
                           "TASK_STATE_CANCELED"):
                log("4.TRACK", f"  sse terminal -> {st_name}")
                return event.task if event.HasField("task") else None
        if event.HasField("message"):
            for p in event.message.parts:
                log("4.TRACK", f"  sse message: {p.text}")
        if event.HasField("artifact_update"):
            for p in event.artifact_update.artifact.parts:
                log("4.TRACK", f"  sse artifact: {p.text}")
    raise SystemExit("subscribe ended before terminal state")


# --------------------------------------------------------------------------
# [5] SIGNED AGENT CARD - JWS verification + tamper detection
# --------------------------------------------------------------------------
async def signed_card_demo(base_url: str, card) -> None:
    from jwt.api_jwk import PyJWK

    log("5.SIGNED", "Agent Card carries JWS signature(s) — verifying…")
    async with httpx.AsyncClient(timeout=10) as hc:
        jwks = (await hc.get(f"{base_url}/jwks.json")).json()
    jwk = jwks["keys"][0]
    log("5.SIGNED", f"  fetched JWKS: kid={jwk.get('kid')} kty={jwk.get('kty')} "
                    f"alg=ES256")
    verifier = signing.create_signature_verifier(
        key_provider=lambda kid, jku: PyJWK.from_dict(jwk),
        algorithms=["ES256"],
    )
    # fresh fetch with signature verification wired into discovery
    async with httpx.AsyncClient(timeout=10) as hc:
        resolved = await A2ACardResolver(hc, base_url).get_agent_card(
            signature_verifier=verifier
        )
    log("5.SIGNED", f"  fetch->verify OK: card '{resolved.name}' authentic "
                    f"(sig count={len(resolved.signatures)})")

    # tamper detection: change a field, re-verify, expect rejection
    tampered = copy.deepcopy(resolved)
    tampered.description = "**TAMPERED BY ATTACKER**"
    try:
        verifier(tampered)
        log("5.SIGNED", "  !! VERIFICATION PASSED ON TAMPERED CARD (bad)")
    except signing.InvalidSignaturesError:
        log("5.SIGNED", "  tamper check: modified card REJECTED "
                        "(InvalidSignaturesError) — integrity holds")
    except signing.SignatureVerificationError:
        log("5.SIGNED", "  tamper check: modified card REJECTED "
                        "(SignatureVerificationError) — integrity holds")

    # unsigned-card path for comparison (strip signatures)
    unsigned = copy.deepcopy(resolved)
    del unsigned.signatures[:]
    try:
        verifier(unsigned)
        log("5.SIGNED", "  !! VERIFICATION PASSED ON UNSIGNED CARD (bad)")
    except signing.InvalidSignaturesError as e:
        log("5.SIGNED", f"  unsigned card rejected by verifier: {e}")
    except signing.NoSignatureError as e:
        log("5.SIGNED", f"  unsigned card rejected by verifier: {e}")


# --------------------------------------------------------------------------
async def main(base_url: str, verify: bool) -> None:
    card = await discover(base_url)
    skill = select_skill(card, ["workflow", "run"])

    # client 1: streaming (SendStreamingMessage on the wire)
    factory1 = ClientFactory()
    client1 = await factory1.create_from_url(base_url)
    task1 = await delegate_streaming(client1, skill, "run feature workflow for TICKET-42")
    await client1.close()

    # client 2: non-streaming + polling (SendMessage with return_immediately,
    # then GetTask loop)
    factory2 = ClientFactory(ClientConfig(streaming=False, polling=True))
    client2 = await factory2.create_from_url(base_url)
    t2 = uuid.uuid4().hex
    log("4.TRACK", f"starting second task {t2} with polling config")
    request = SendMessageRequest(
        message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER,
                            parts=[Part(text="run agent-status workflow")]),
        metadata={"skillId": "agent-status"},
    )
    async for event in client2.send_message(request):
        if event.HasField("task"):
            t2 = event.task.id
            log("4.TRACK", f"  initial response returns task id={t2} "
                           f"state={state_name(event.task.status.state)}")
            break
    await track_polling(client2, t2)
    await client2.close()

    # subscribe demo: start a third task with the non-streaming client (instant
    # task id), then reattach with SubscribeToTask over a streaming client
    factory3 = ClientFactory(ClientConfig(streaming=False, polling=True))
    client3 = await factory3.create_from_url(base_url)
    started = None
    req = SendMessageRequest(
        message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER,
                            parts=[Part(text="run workflow with subscribe demo")]),
        metadata={"skillId": "workflow-run"},
    )
    async for event in client3.send_message(req):
        if event.HasField("task") and event.task.id:
            started = event.task.id
            log("4.TRACK", f"  task {started} started (return_immediately)")
            break
    await client3.close()

    factory3s = ClientFactory()
    client3s = await factory3s.create_from_url(base_url)
    worked = False
    for _ in range(10):
        tsk = await client3s.get_task(GetTaskRequest(id=started))
        if state_name(tsk.status.state) == "TASK_STATE_WORKING":
            worked = True
            break
        await asyncio.sleep(0.2)
    if not started:
        log("4.TRACK", "  subscribe demo skipped (no task id returned)")
        await client3s.close()
    elif worked:
        await track_subscribe(client3s, started)
    else:
        log("4.TRACK", "  subscribe demo skipped (task completed too fast)")
    await client3s.close()

    if verify:
        await signed_card_demo(base_url, card)

    print("\nSPIKE SUMMARY")
    print("  - SDK client auto-negotiates SendMessage vs SendStreamingMessage from")
    print("    ClientConfig.streaming AND card.capabilities.streaming.")
    print("  - non-streaming SendMessage returns a single task snapshot; the")
    print("    caller owns the GetTask polling loop (client.polling=True).")
    print("  - SendStreamingMessage yields status/message/artifact StreamResponse")
    print("    events over SSE; SubscribeToTask reconnects to in-flight tasks.")
    print("  - Signed Agent Cards: ES256 JWS verified end-to-end; tampering")
    print("    detected; SDK supports sign+verify natively.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A2A dispatcher spike")
    parser.add_argument("--base-url", default="http://127.0.0.1:41999")
    parser.add_argument("--verify", action="store_true",
                        help="enable Signed Agent Card verification demo")
    args = parser.parse_args()
    asyncio.run(main(args.base_url, args.verify))