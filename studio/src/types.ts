export type Kind = "exec" | "judgment" | "gate";
export interface Step {
  id: string;
  kind: Kind;
  role?: string | null;
  on_failure?: string | null;
  requires?: string | null;
  max_retries?: number | null;
  max_turns?: number | null;
  tools?: string[];
  in?: Record<string, string>;
  out?: Record<string, string>;
  enums?: Record<string, string[]>;
  fail_on?: Record<string, string[]>;
  show?: string[];
  routes?: Route[];
  skip_when_unchanged?: boolean;
  approve_as?: string;
  /** plain-language sentence of what the step does (live mode) */
  summary?: string;
}
export type WhenVal = string | string[];
export interface Route { when?: Record<string, WhenVal>; goto: string }
export interface Workflow {
  name: string;
  version: number;
  plugin?: string;
  /** what starts a run when it is not a person or a pipeline, e.g. "Nightly" */
  trigger?: string;
  /** plain-language description shown in the inspector */
  summary?: string;
  inputs: Record<string, { type: string }>;
  steps: Step[];
}
export type StepState = "done" | "running" | "failed" | "waiting" | "pending";
export interface Pipeline {
  id: string;
  name: string;
  workflow: string;
  connector: string;
  event: string;
  filter: string;
}
