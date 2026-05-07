"""LLM prompts for ORFS-Agent setup phase.

The LLM may only see: prompt.txt, FlowVariables.md, the design's config/* and
src/* contents. It must NOT see info.json. Targets are inferred by the LLM
from prompt.txt natural-language statements like "Target area: <= 127 um^2".
"""

SETUP_SYSTEM_PROMPT = """You are an expert physical-design engineer setting up a Bayesian-optimization-style search over OpenROAD flow parameters.

You will be given:
  1. The task prompt (natural-language target spec).
  2. The design's existing config files (config/*.mk, config/*.sdc).
  3. A list (and small contents) of source files in src/.
  4. A reference doc FlowVariables.md describing every tunable variable.

Your job is to produce a structured search space:

A. Identify which OpenROAD flow variables are worth tuning for THIS task. Use only variables that:
   - Are documented in FlowVariables.md.
   - Have a real effect on the targeted PPA dimension(s) inferred from the prompt.
   - Are safe to tune without breaking the flow.
   Prefer 4-10 parameters; avoid trivial/locked ones (PLATFORM, DESIGN_NAME, DESIGN_NICKNAME, VERILOG_FILES, SDC_FILE).

B. For each tunable parameter:
   - name: variable name as it appears in config.mk (or a label for SDC clock-period tuning).
   - type: "int" | "float" | "categorical".
   - range: [lo, hi] for numeric (use sensible bounds from FlowVariables.md / common practice), or list of values for categorical.
   - default: the current value parsed from the design's config; if not present in any file, use a sensible center.
   - file: relative path inside the sandbox where the value lives or should be written (e.g. "config/foo.mk" or "config/foo.sdc").
   - var_kind: one of:
       * "make_export"             — written as `export NAME = VALUE` in a .mk file
       * "sdc_clk_period"          — replaces `set clk_period <val>` in a .sdc
       * "sdc_create_clock_period" — replaces `-period <val>` in a `create_clock` line
   - rationale: one short sentence on why this parameter helps the targeted dimension(s).

C. Do NOT include info.json — you do not have access to it. Infer targets only from the natural-language prompt.

Return your answer by calling the `submit_search_space` tool exactly once. No prose response.
"""


def build_setup_user_message(prompt_text: str,
                              config_files: dict,
                              src_files_list: list,
                              flow_vars_excerpt: str) -> str:
    """Compose the LLM user message for the setup phase.

    `config_files`: dict {relative_path -> file content (truncated if huge)}
    `src_files_list`: list of relative paths in src/ (we don't dump full content
                      to keep tokens low; the LLM only needs param-relevance,
                      not RTL semantics)
    `flow_vars_excerpt`: contents of FlowVariables.md (or a relevant slice)
    """
    parts = []
    parts.append("=== TASK PROMPT (prompt.txt) ===")
    parts.append(prompt_text.strip())
    parts.append("")
    parts.append("=== DESIGN CONFIG FILES ===")
    for rel, content in config_files.items():
        parts.append(f"--- {rel} ---")
        parts.append(content.strip())
        parts.append("")
    parts.append("=== SOURCE FILES IN src/ ===")
    parts.append("\n".join(src_files_list) if src_files_list else "(none)")
    parts.append("")
    parts.append("=== FlowVariables.md (reference) ===")
    parts.append(flow_vars_excerpt.strip())
    parts.append("")
    parts.append("Now call `submit_search_space` with your chosen parameters.")
    return "\n".join(parts)


# JSON schema for the structured tool the LLM must call.
SUBMIT_SEARCH_SPACE_SCHEMA = {
    "name": "submit_search_space",
    "description": "Submit the chosen tunable parameters and ranges for the optimization run.",
    "parameters": {
        "type": "object",
        "properties": {
            "tunable_params": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "name":      {"type": "string"},
                        "type":      {"type": "string", "enum": ["int", "float", "categorical"]},
                        "range":     {"type": "array"},
                        "default":   {},
                        "file":      {"type": "string"},
                        "var_kind":  {"type": "string", "enum":
                                       ["make_export", "sdc_clk_period",
                                        "sdc_create_clock_period"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["name", "type", "range", "default",
                                 "file", "var_kind"],
                },
            },
        },
        "required": ["tunable_params"],
    },
}
