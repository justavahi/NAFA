"""Small reproducible extension to the pinned Backend gateway snapshot.

Keeps its HTTP/retry/cache/budget implementation; options enter the request hash.
"""
from pathlib import Path
import os


def patch_gateway(path: Path) -> None:
    source = path.read_text()
    original=source
    signature = "temperature: float = 0.0, response_format: dict | None = None, use_cache: bool = True) -> LLMResult:"
    payload = '        if response_format:\n            payload["response_format"] = response_format\n'
    if "# PE_REQUEST_OPTIONS_V1" not in source:
        if signature not in source or payload not in source:
            raise ValueError("Backend gateway revision differs; refusing an unsafe patch")
        source = source.replace(signature, "temperature: float = 0.0, response_format: dict | None = None, use_cache: bool = True, request_options: dict | None = None) -> LLMResult:")
        source = source.replace(payload, payload + '''        # PE_REQUEST_OPTIONS_V1: explicit controls, included before cache hashing.
        if request_options:
            if set(request_options) - {"reasoning_effort", "reasoning", "thinking"}:
                raise ValueError("unsupported request option")
            payload.update(request_options)
''')
    if "# PE_RESPONSE_V2" not in source:
        old='content = data["choices"][0]["message"]["content"]'
        if old not in source:
            raise ValueError("Backend response parser differs")
        source=source.replace(old,old+' or ""  # PE_RESPONSE_V2: empty reasoning-only response is not SQL NULL')
        source=source.replace('                    return content, tin, tout','                    self._pe_finish_reason = data["choices"][0].get("finish_reason")\n                    return content, tin, tout')
        cache='''        self.conn.execute("INSERT OR REPLACE INTO llm_cache(request_hash,response,model,created_at) VALUES(?,?,?,?)",
                          (req_hash, text, m["model"], iso(now_utc())))'''
        if cache not in source:
            raise ValueError("Backend cache writer differs")
        source=source.replace(cache,'''        if text and getattr(self, "_pe_finish_reason", None) != "length":
            self.conn.execute("INSERT OR REPLACE INTO llm_cache(request_hash,response,model,created_at) VALUES(?,?,?,?)",
                              (req_hash, text, m["model"], iso(now_utc())))''')
    if source!=original:
        temporary=path.with_name(path.name+f".pe-{os.getpid()}.tmp")
        temporary.write_text(source)
        temporary.replace(path)
