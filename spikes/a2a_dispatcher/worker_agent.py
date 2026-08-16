"""A2A dispatcher spike - worker agent (official `a2a-sdk` v1.1.2).

A minimal A2A worker that advertises an Agent Card (discovery), accepts
delegated tasks over JSON-RPC (SendMessage / SendStreamingMessage), simulates
long-running orchestrator workflows, and (optionally) serves a *Signed Agent
Card* plus its JWKS endpoint.

Run:
    python3 worker_agent.py --port 41999            # unsigned card
    python3 worker_agent.py --port 41999 --signed   # signed card + /jwks.json

Based on the official SDK sample (`samples/hello_world_agent.py`), trimmed to
the transports this spike exercises (JSON-RPC 1.0 + HTTP+JSON 1.0).
"""

import argparse
import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI

from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils import signing

logger = logging.getLogger("worker_agent")


class OrchestratorWorkerExecutor(AgentExecutor):
    """Simulates an orchestrator workflow runner behind the A2A interface.

    A delegated task is treated as a workflow invocation: it emits progress
    events (step messages), optionally writes an intermediate artifact, then
    completes with a final report artifact. Cancellation is honoured between
    simulated steps.
    """

    def __init__(self, step_delay: float = 0.8) -> None:
        self.running_tasks: set[str] = set()
        self.step_delay = step_delay

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id
        if task_id in self.running_tasks:
            self.running_tasks.remove(task_id)
        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id or "",
            context_id=context.context_id or "",
        )
        await updater.cancel()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_message = context.message
        task_id = context.task_id
        context_id = context.context_id
        if not user_message or not task_id or not context_id:
            return

        self.running_tasks.add(task_id)
        # The SDK handler requires the Task to be enqueued before status
        # updates: enqueue submitted-state Task first (with history), then
        # transition states.
        await event_queue.enqueue_event(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                history=[user_message],
            )
        )

        updater = TaskUpdater(
            event_queue=event_queue,
            task_id=task_id,
            context_id=context_id,
        )

        # submitted -> working
        working_message = updater.new_agent_message(
            parts=[Part(text="Accepted. Running orchestrator workflow…")]
        )
        await updater.start_work(
            message=working_message
        )

        query = context.get_user_input()
        skill_id = (context.metadata or {}).get("skillId", "workflow-run")

        steps = [
            "Step 1/3 — loading ticket context (backlog → A2A delegation)",
            "Step 2/3 — selecting orchestrator workflow + agent steps",
            f"Step 3/3 — executing workflow '{skill_id}'",
        ]
        for i, step in enumerate(steps, start=1):
            if task_id not in self.running_tasks:
                return  # canceled mid-flight
            await asyncio.sleep(self.step_delay)
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(parts=[Part(text=step)]),
            )

        if task_id not in self.running_tasks:
            return

        # add an intermediate artifact, then the final one
        await updater.add_artifact(
            parts=[Part(text=f"workflow trace for '{query}'\n> {steps[0]}")],
            name="workflow-trace",
            last_chunk=False,
        )
        await asyncio.sleep(self.step_delay)
        await updater.add_artifact(
            parts=[
                Part(
                    text=(
                        "WORKFLOW COMPLETE\n"
                        f"skill selected: {skill_id}\n"
                        f"query: {query}\n"
                        "result: simulation succeeded (this is a protocol spike, "
                        "not a real workflow run)"
                    )
                )
            ],
            name="final-report",
            last_chunk=True,
        )
        await updater.complete()
        logger.info("Task %s finished: completed", task_id)


def build_agent_card(host: str, port: int) -> AgentCard:
    return AgentCard(
        name="Orchestrator Worker Agent",
        description=(
            "Simulated orchestrator worker: accepts delegated workflow tasks "
            "via A2A and reports progress + final artifacts."
        ),
        provider=AgentProvider(organization="Hermes Platform", url="http://localhost"),
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=["text"],
        default_output_modes=["text", "task-status"],
        skills=[
            AgentSkill(
                id="workflow-run",
                name="Run orchestrator workflow",
                description=(
                    "Delegates a backlog ticket to an orchestrator workflow "
                    "and streams progress until completion."
                ),
                tags=["workflow", "run", "orchestrate", "backlog"],
                examples=["run the feature workflow for TICKET-42"],
                input_modes=["text"],
                output_modes=["text", "task-status"],
            ),
            AgentSkill(
                id="agent-status",
                name="Worker status report",
                description="Reports liveness/status of the worker.",
                tags=["status", "health", "ping"],
                examples=["status report"],
                input_modes=["text"],
                output_modes=["text", "task-status"],
            ),
        ],
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version="1.0",
                url=f"http://{host}:{port}/a2a/jsonrpc",
            ),
            AgentInterface(
                protocol_binding="HTTP+JSON",
                protocol_version="1.0",
                url=f"http://{host}:{port}/a2a/rest",
            ),
        ],
    )


def build_signed_card(card: AgentCard, port: int, kid: str = "worker-1"):
    """Sign the Agent Card with an ES256 key (SDK `create_agent_card_signer`).

    Returns (signed_card, private_jwk_dict, public_jwk_dict).
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.api_jwk import PyJWK

    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key()
    x = pub.public_numbers().x
    y = pub.public_numbers().y

    def _b64(n: int) -> str:
        return __import__("base64").urlsafe_b64encode(
            n.to_bytes((n.bit_length() + 7) // 8, "big")
        ).rstrip(b"=").decode()

    public_jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(x),
        "y": _b64(y),
        "kid": kid,
        "use": "sig",
    }
    private_jwk = {
        **public_jwk,
        "d": _b64(key.private_numbers().private_value),
    }

    signer = signing.create_agent_card_signer(
        signing_key=PyJWK.from_dict(private_jwk),
        protected_header={
            "alg": "ES256",
            "typ": "JOSE",
            "kid": kid,
            "jku": f"http://127.0.0.1:{port}/jwks.json",
        },
    )
    signed_card = signer(card)
    return signed_card, private_jwk, public_jwk


def build_app(port: int, signed: bool) -> FastAPI:
    host = "127.0.0.1"
    card = build_agent_card(host, port)

    public_jwk = None
    if signed:
        card, _, public_jwk = build_signed_card(card, port)
        logger.info("Agent Card signed (ES256, kid=worker-1), %d signature(s)",
                    len(card.signatures))

    task_store = InMemoryTaskStore()
    request_handler = DefaultRequestHandler(
        agent_executor=OrchestratorWorkerExecutor(),
        task_store=task_store,
        agent_card=card,
    )
    app = FastAPI(title="A2A Dispatcher Spike Worker")
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card=card),
        jsonrpc_routes=create_jsonrpc_routes(
            request_handler=request_handler,
            rpc_url="/a2a/jsonrpc",
        ),
        rest_routes=create_rest_routes(
            request_handler=request_handler,
            path_prefix="/a2a/rest",
        ),
    )

    if signed:
        @app.get("/jwks.json")
        def jwks() -> dict:
            return {"keys": [public_jwk]}

    return app


async def serve(port: int, signed: bool) -> None:
    app = build_app(port, signed)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("Worker serving Agent Card at http://127.0.0.1:%s/.well-known/agent-card.json", port)
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=41999)
    parser.add_argument("--signed", action="store_true")
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(port=args.port, signed=args.signed))