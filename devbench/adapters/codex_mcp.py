"""Optional existing MCP transport; normal approvals and sandbox are retained."""
from devbench_engine.adapters import CodexCLIAdapter


class CodexMCPAdapter(CodexCLIAdapter):
    """Explicit MCP alias; no automatic approval suppression or sandbox bypass."""
    pass
