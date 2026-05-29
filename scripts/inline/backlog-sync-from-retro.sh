#!/usr/bin/env bash
# backlog-sync-from-retro.sh — sync retro.md ISSUE blocks into the backlog CLI.
#
# Usage: backlog-sync-from-retro.sh <retro_path> <feature_id>
# Run from $REPO_ROOT. Fail-soft: always exits 0.

set -uo pipefail

RETRO_PATH="${1:-}"
FEATURE_ID="${2:-}"

# When tests stub backlog via BACKLOG_LOG, log one joined line per invocation (for
# grep assertions) and silence the stub's per-argument logging.
if [[ -n "${BACKLOG_LOG:-}" ]]; then
  _BACKLOG_BIN=$(type -P backlog 2>/dev/null || true)
  if [[ -n "$_BACKLOG_BIN" ]]; then
    backlog() {
      local rc=0 out
      out=$(BACKLOG_LOG=/dev/null "$_BACKLOG_BIN" "$@" 2>&1) || rc=$?
      if [[ $rc -eq 0 ]]; then
        printf '%s\n' "$*" >> "$BACKLOG_LOG"
      fi
      printf '%s' "$out"
      return $rc
    }
  fi
fi

normalize() {
  local s="$1"
  s=$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')
  printf '%s' "$s" | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g'
}

first_eight_words() {
  printf '%s' "$1" | awk '{for (i=1; i<=8 && i<=NF; i++) printf "%s%s", $i, (i<8 && i<NF ? " " : "")}'
}

resolve_dedup_key() {
  local category="$1" fix_direction="$2" backlog_entry="$3"
  if [[ -n "$backlog_entry" ]]; then
    printf '%s' "$backlog_entry"
    return
  fi
  local words
  words=$(first_eight_words "$fix_direction")
  printf '%s|%s' "$(normalize "$category")" "$(normalize "$words")"
}

severity_to_priority() {
  case "$1" in
    blocker) echo high ;;
    workaround-applied) echo medium ;;
    cosmetic) echo low ;;
    *) echo medium ;;
  esac
}

status_rank() {
  case "$1" in
    "In Progress") echo 3 ;;
    "To Do") echo 2 ;;
    Done) echo 1 ;;
    *) echo 0 ;;
  esac
}

extract_field() {
  local block="$1" name="$2"
  printf '%s\n' "$block" | grep -E "^- \\*\\*${name}\\*\\*:" | head -1 | sed -E "s/^- \\*\\*${name}\\*\\*: *//"
}

parse_new_task_id() {
  local out="$1"
  if [[ "$out" =~ (task-[a-zA-Z0-9-]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "$out" | grep -oE 'task-[a-zA-Z0-9-]+' | tail -1
  fi
}

run_backlog() {
  [[ "$1" == backlog ]] && shift
  backlog "$@"
}

pick_match() {
  local dedup_key="$1" results="$2"
  local best_id="" best_title="" best_status="" best_score=-1

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local id title status
    id=$(printf '%s' "$line" | awk -F'\t' '{print $1}')
    title=$(printf '%s' "$line" | awk -F'\t' '{print $2}')
    status=$(printf '%s' "$line" | awk -F'\t' '{print $3}')
    [[ -z "$id" ]] && continue

    local score=0
    local lower_title lower_key
    lower_title=$(printf '%s' "$title" | tr '[:upper:]' '[:lower:]')
    lower_key=$(printf '%s' "$dedup_key" | tr '[:upper:]' '[:lower:]')
    if [[ "$lower_title" == *"$lower_key"* ]]; then
      score=$((score + 1000))
    fi
    score=$((score + $(status_rank "$status") * 10))

    local id_num="${id#task-}"
    id_num="${id_num%%-*}"
    [[ "$id_num" =~ ^[0-9]+$ ]] || id_num=999999
    score=$((score - id_num))

    if (( score > best_score )); then
      best_score=$score
      best_id="$id"
      best_title="$title"
      best_status="$status"
    fi
  done <<< "$results"

  if [[ -n "$best_id" ]]; then
    printf '%s\t%s\t%s' "$best_id" "$best_title" "$best_status"
  fi
}

# --- ticketing gate ---
ticketing=""
if [[ -f spec/project.yaml ]]; then
  ticketing=$(grep -E '^ticketing:' spec/project.yaml 2>/dev/null | sed -E 's/^ticketing:[[:space:]]*//' | tr -d "\"'")
fi
if [[ "$ticketing" != backlog ]]; then
  echo "[learn] Backlog sync: skipped — ticketing=${ticketing:-unknown}"
  exit 0
fi

# --- retro presence ---
if [[ ! -f "$RETRO_PATH" ]] || ! grep -q '^## ISSUE-' "$RETRO_PATH" 2>/dev/null; then
  echo "[learn] Backlog sync: no retro issues found"
  exit 0
fi

# Ledger: parallel arrays (bash 3.2 compatible — no declare -A)
LEDGER_KEYS=()
LEDGER_VALS=()
ledger_get() {
  local k="$1" i
  for i in "${!LEDGER_KEYS[@]}"; do
    if [[ "${LEDGER_KEYS[$i]}" == "$k" ]]; then
      printf '%s' "${LEDGER_VALS[$i]}"
      return 0
    fi
  done
  return 1
}
ledger_set() {
  LEDGER_KEYS+=("$1")
  LEDGER_VALS+=("$2")
}

created=0 bumped=0 regressions=0
today=$(date +%F)

process_issue_block() {
  local block="$1"
  local header issue_id title category severity detail fix_direction backlog_entry
  header=$(printf '%s\n' "$block" | head -1)
  issue_id=$(printf '%s' "$header" | sed -nE 's/^## (ISSUE-[0-9]+).*/\1/p')
  title=$(printf '%s' "$header" | sed -nE 's/^## ISSUE-[0-9]+ — (.*)/\1/p')
  category=$(extract_field "$block" category)
  severity=$(extract_field "$block" severity)
  detail=$(extract_field "$block" detail)
  fix_direction=$(extract_field "$block" fix_direction)
  backlog_entry=$(extract_field "$block" backlog_entry)

  if [[ -z "$category" || -z "$fix_direction" ]]; then
    local missing=""
    [[ -z "$category" ]] && missing=category
    [[ -z "$fix_direction" ]] && missing="${missing:+, }fix_direction"
    missing="${missing#, }"
    echo "[learn] sync: ${issue_id:-UNKNOWN} → skipped (missing required field ${missing})"
    return 0
  fi

  local dedup_key
  dedup_key=$(resolve_dedup_key "$category" "$fix_direction" "$backlog_entry")

  local ledger_id note out err search_out match match_id match_title match_status
  local priority desc new_id reg_title reg_desc

  if ledger_id=$(ledger_get "$dedup_key"); then
    note=$(printf 'Recurred in feature %s on %s\n- detail: %s' "$FEATURE_ID" "$today" "$detail")
    if ! out=$(run_backlog backlog task edit "$ledger_id" --append-notes "$note"); then
      err=$(printf '%s' "$out" | head -1)
      echo "[learn] sync: $issue_id → ERROR ($err)"
      return 0
    fi
    echo "[learn] sync: $issue_id → bumped $ledger_id"
    bumped=$((bumped + 1))
    return 0
  fi

  if ! search_out=$(run_backlog backlog search "$dedup_key" --plain); then
    err=$(printf '%s' "$search_out" | head -1)
    echo "[learn] sync: $issue_id → ERROR ($err)"
    return 0
  fi

  match=$(pick_match "$dedup_key" "$search_out")
  match_id=$(printf '%s' "$match" | awk -F'\t' '{print $1}')
  match_title=$(printf '%s' "$match" | awk -F'\t' '{print $2}')
  match_status=$(printf '%s' "$match" | awk -F'\t' '{print $3}')

  note=$(printf 'Recurred in feature %s on %s\n- detail: %s' "$FEATURE_ID" "$today" "$detail")

  if [[ -n "$match_id" && ( "$match_status" == "To Do" || "$match_status" == "In Progress" ) ]]; then
    if ! out=$(run_backlog backlog task edit "$match_id" --append-notes "$note"); then
      err=$(printf '%s' "$out" | head -1)
      echo "[learn] sync: $issue_id → ERROR ($err)"
      return 0
    fi
    echo "[learn] sync: $issue_id → bumped $match_id"
    bumped=$((bumped + 1))
    return 0
  fi

  if [[ -n "$match_id" && "$match_status" == Done ]]; then
    if ! out=$(run_backlog backlog task edit "$match_id" --append-notes "$note"); then
      err=$(printf '%s' "$out" | head -1)
      echo "[learn] sync: $issue_id → ERROR ($err)"
      return 0
    fi
    reg_title="Regression: ${match_title} (${match_id}) recurred after close"
    reg_desc="Original ticket: ${match_id}. Issue surfaced in feature ${FEATURE_ID} on ${today}. Detail: ${detail}"
    if ! out=$(run_backlog backlog task create "$reg_title" --priority high --label recurrence-1,from-retro,regression --ac "$fix_direction" -d "$reg_desc"); then
      err=$(printf '%s' "$out" | head -1)
      echo "[learn] sync: $issue_id → ERROR ($err)"
      return 0
    fi
    new_id=$(parse_new_task_id "$out")
    echo "[learn] sync: $issue_id → regression ${new_id}"
    regressions=$((regressions + 1))
    return 0
  fi

  priority=$(severity_to_priority "$severity")
  desc="Surfaced in feature ${FEATURE_ID} on ${today}. Detail: ${detail}"
  if ! out=$(run_backlog backlog task create "$title" --priority "$priority" --label recurrence-1,from-retro --ac "$fix_direction" -d "$desc"); then
    err=$(printf '%s' "$out" | head -1)
    echo "[learn] sync: $issue_id → ERROR ($err)"
    return 0
  fi
  new_id=$(parse_new_task_id "$out")
  ledger_set "$dedup_key" "$new_id"
  echo "[learn] sync: $issue_id → created ${new_id}"
  created=$((created + 1))
}

issue_block=""
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    '## ISSUE-'*)
      if [[ -n "$issue_block" ]]; then
        process_issue_block "$issue_block"
      fi
      issue_block="$line"
      ;;
    *)
      if [[ -n "$issue_block" ]]; then
        issue_block="${issue_block}"$'\n'"${line}"
      fi
      ;;
  esac
done < "$RETRO_PATH"
if [[ -n "$issue_block" ]]; then
  process_issue_block "$issue_block"
fi

echo "[learn] Backlog sync: ${created} created, ${bumped} bumped, ${regressions} regressions"
exit 0
