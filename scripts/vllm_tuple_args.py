"""Keep vLLM's Gemma 4 tool parser from breaking up tuples.

The DRC tools describe points and index ranges as Python tuples, e.g.
`solutions` as [(llx, lly), (urx, ury)] and `second_level_index_range` as
(10, 20). Most backends hand that text to the tools intact, as a string or
as numbers, and the tools read it (parse_numbers in agents/drc/tools/utils.py).
Gemma's own call syntax has no tuples, though, and vLLM's gemma4 parser
splits (10, 20) at the comma into '(10' and '20)'. As an argument's whole
value it also renames or drops the arguments after it, so a call written
as described is lost before it reaches the tools.

scripts/serve_vllm.sh loads this file with --tool-parser-plugin. It makes
the parser read a tuple (...) outside Gemma's string delimiters as a list
[...], inside the vLLM server; vLLM's files stay unchanged. Written against
vLLM 0.32's vllm.parser.gemma4.
"""

import vllm.parser.gemma4 as gemma4

_TUPLE_TO_LIST = str.maketrans("()", "[]")
_parse_gemma4_fields = gemma4._parse_gemma4_fields


def _parse_gemma4_fields_with_tuples(args_str, *, partial=False):
    parts = args_str.split(gemma4.STRING_DELIM)
    parts[::2] = [part.translate(_TUPLE_TO_LIST) for part in parts[::2]]
    return _parse_gemma4_fields(gemma4.STRING_DELIM.join(parts), partial=partial)


gemma4._parse_gemma4_fields = _parse_gemma4_fields_with_tuples
