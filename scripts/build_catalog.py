"""Build the playground catalog from the published NAI 2.8 OpenAPI document."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "openapi" / "nai-2.8.0.json"
OUT_PATH = ROOT / "static" / "catalog.json"

LIST_OPTIONS = {"filters": [], "limit": 20, "offset": 0, "sort": []}

OVERRIDES = {
    ("POST", "/api/enterpriseai/v1/credentials"): {
        "name": "openai-lab",
        "type": "openai",
        "data": {
            "openai": {
                "apiKey": "replace-me",
                "hostname": "api.openai.com",
                "port": 443,
            }
        },
    },
    ("POST", "/api/enterpriseai/v1/providers"): {
        "name": "anthropic-lab",
        "type": "anthropic",
        "credentialId": "credential-id",
    },
    ("POST", "/api/enterpriseai/v1/unified-endpoints"): {
        "name": "chat-gateway",
        "mode": "loadbalancer",
        "apiSpec": "openaiChatV1APISpec",
        "isRetryEnabled": True,
        "apiKeys": [],
        "targetEndpoints": [
            {
                "endpointType": "internal",
                "internalEndpointId": "endpoint-id",
                "weight": 100,
            }
        ],
    },
    ("PUT", "/api/enterpriseai/v1/unified-endpoints/{unified_endpoint_id}"): {
        "mode": "fallback",
        "apiSpec": "openaiChatV1APISpec",
        "isRetryEnabled": True,
        "targetEndpoints": [
            {
                "endpointType": "provider",
                "providerId": "provider-id",
                "providerModelName": "claude-sonnet-4-5",
                "priority": 1,
            }
        ],
    },
    ("PATCH", "/api/enterpriseai/v1/unified-endpoints/{unified_endpoint_id}/ratelimit"): {
        "global": {
            "rateLimitConfig": {
                "cost": "total_tokens",
                "durationUnit": "Day",
                "value": 100000,
            }
        },
        "header": {
            "name": "x-user-id",
            "rateLimitConfig": {
                "cost": "total_tokens",
                "durationUnit": "Day",
                "value": 20000,
            },
            "valueOverrides": [],
        },
    },
    ("POST", "/api/enterpriseai/v1/apikeys"): {
        "name": "gateway-key",
        "endpoints": [],
        "unifiedEndpoints": ["unified-endpoint-id"],
    },
    ("POST", "/api/enterpriseai/v1/mcp/servers/"): {
        "name": "remote-tools",
        "serverType": "remote",
        "remoteMcpServer": {"url": "https://mcp.example.com/mcp"},
    },
    ("POST", "/api/enterpriseai/v1/mcp/connectors"): {
        "name": "agent-tools",
        "mcpKeys": [],
        "mcpServers": [
            {"serverId": "mcp-server-id", "allowedTools": ["tool_name"]}
        ],
    },
    ("POST", "/api/enterpriseai/v1/mcpkeys"): {
        "name": "agent-key",
        "connectors": ["mcp-connector-id"],
    },
    ("POST", "/enterpriseai/v1/chat/completions"): {
        "model": "model-id",
        "messages": [
            {"role": "user", "content": "Hello from the NAI playground."}
        ],
        "stream": False,
    },
    ("POST", "/enterpriseai/gateway/v1/chat/completions"): {
        "model": "unified-endpoint-name",
        "messages": [
            {"role": "user", "content": "Hello from the Agent Gateway."}
        ],
        "stream": False,
    },
    ("POST", "/enterpriseai/v1/embeddings"): {
        "model": "model-id",
        "input": "Nutanix Enterprise AI",
    },
    ("POST", "/enterpriseai/gateway/v1/embeddings"): {
        "model": "unified-endpoint-name",
        "input": "Nutanix Agent Gateway",
    },
    ("POST", "/enterpriseai/v1/images/generations"): {
        "model": "model-id",
        "prompt": "A quiet server room at night, soft green light",
        "n": 1,
        "size": "1024x1024",
    },
    ("POST", "/enterpriseai/gateway/v1/images/generations"): {
        "model": "unified-endpoint-name",
        "prompt": "A quiet server room at night, soft green light",
        "n": 1,
        "size": "1024x1024",
    },
    ("POST", "/enterpriseai/gateway/v1/messages"): {
        "model": "unified-endpoint-name",
        "max_tokens": 256,
        "messages": [
            {"role": "user", "content": "Hello from the Agent Gateway."}
        ],
    },
    ("POST", "/enterpriseai/v1/rerank"): {
        "model": "model-id",
        "query": "What does Agent Gateway govern?",
        "documents": [
            {"text": "Agent Gateway is the front door for models and MCP tools."},
            {"text": "Private inference runs models inside the NAI cluster."},
        ],
        "top_n": 2,
    },
    ("POST", "/enterpriseai/v2/rerank"): {
        "model": "unified-endpoint-name",
        "query": "What does Agent Gateway govern?",
        "documents": [
            {"text": "Agent Gateway is the front door for models and MCP tools."},
            {"text": "Private inference runs models inside the NAI cluster."},
        ],
        "top_n": 2,
        "return_documents": True,
    },
}

NOTES = {
    ("POST", "/enterpriseai/gateway/v1/chat/completions"): (
        "Documented Agent Gateway path for a unified endpoint or KV-aware endpoint "
        "with text-to-text. The model field is the unified endpoint name. "
        "Audio stays on /enterpriseai/v1/."
    ),
    ("POST", "/enterpriseai/gateway/v1/embeddings"): (
        "Documented Agent Gateway path for a unified endpoint serving an embedding model."
    ),
    ("GET", "/enterpriseai/gateway/v1/models"): (
        "Documented Agent Gateway path for listing models behind the gateway."
    ),
    ("POST", "/enterpriseai/gateway/v1/images/generations"): (
        "Gateway form of image generation. The 2.8 base-path rule covers every "
        "unified endpoint except audio. apiSpec openaiImageGenerationsV1APISpec "
        "selects this contract. Confirm the Access URL on the endpoint page."
    ),
    ("POST", "/enterpriseai/gateway/v1/messages"): (
        "Anthropic Messages is served only through the Agent Gateway. "
        "model is the unified endpoint name. The published schema marks model "
        "as the only required field; max_tokens and messages match the "
        "Anthropic Messages API the NAI docs point at."
    ),
    ("POST", "/enterpriseai/v2/rerank"): (
        "Rerank v2 exists so unified endpoints can serve Cohere-compatible rerank "
        "(apiSpec cohereRerankV2APISpec). The published path is /enterpriseai/v2/rerank. "
        "The gateway examples do not list a separate rerank URL."
    ),
    ("POST", "/enterpriseai/v1/chat/completions"): (
        "Direct inference on an endpoint API key. model is the id returned by List models. "
        "Use the gateway path when the key is bound to a unified endpoint."
    ),
    ("POST", "/api/enterpriseai/v1/mcp/servers/"): (
        "serverType remote needs remoteMcpServer.url. serverType local needs "
        "localMcpServer with packageType docker, packageName, port, instances, "
        "cpuMilliCores, and memoryInMi."
    ),
    ("PATCH", "/api/enterpriseai/v1/unified-endpoints/{unified_endpoint_id}/ratelimit"): (
        "Header rate limits identify an end user without issuing each person an API key. "
        "durationUnit values are Second, Minute, Hour, Day, Month, Year. "
        "cost values are request, input_tokens, output_tokens, total_tokens."
    ),
}

GATEWAY_REWRITES = {
    "/enterpriseai/v1/chat/completions": "/enterpriseai/gateway/v1/chat/completions",
    "/enterpriseai/v1/embeddings": "/enterpriseai/gateway/v1/embeddings",
    "/enterpriseai/v1/images/generations": "/enterpriseai/gateway/v1/images/generations",
    "/enterpriseai/v1/models": "/enterpriseai/gateway/v1/models",
}

DISCOVER = {
    ("GET", "/api/enterpriseai/v1/version"),
    ("GET", "/api/enterpriseai/v1/users/me"),
    ("GET", "/api/enterpriseai/v1/cluster/health"),
    ("GET", "/api/enterpriseai/v1/cluster/info"),
    ("GET", "/api/enterpriseai/v1/cluster/config"),
}

GATEWAY_ADMIN_TAGS = {
    "unified-endpoints",
    "mcp-servers",
    "mcp-connectors",
    "mcpkeys",
    "providers",
    "credentials",
    "apikeys",
}
INFERENCE_TAGS = {
    "chat",
    "embeddings",
    "images",
    "audio",
    "rerank",
    "inference-models",
    "messages",
}
MODEL_TAGS = {
    "models",
    "endpoints",
    "catalogs",
    "batches",
    "finetune-jobs",
    "data-sources",
}
OBSERVE_TAGS = {"logs", "metrics", "auditlogs", "scans"}

GROUPS = [
    ("cluster", "Cluster", "Read-only checks that tell you the NAI instance is reachable."),
    ("gateway-call", "Agent Gateway calls", "Inference through unified endpoints. Bearer API key."),
    ("gateway-admin", "Agent Gateway admin", "Unified endpoints, providers, MCP servers, connectors, and keys. Basic auth."),
    ("inference", "Direct inference", "OpenAI-compatible calls on a single endpoint. Bearer API key."),
    ("models", "Models and endpoints", "Catalog, model instances, classic endpoints, batches, and fine-tuning."),
    ("observe", "Observe", "Logs, metrics, audit, and security scans."),
    ("platform", "Platform", "Licenses and the remaining cluster configuration calls."),
]


def extract_curl_json(op):
    for sample in op.get("x-codeSamples") or []:
        if sample.get("lang") != "cURL":
            continue
        match = re.search(r"--data\s+'(\{.*\})'", sample.get("source") or "", re.S)
        if not match:
            continue
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def is_list_options(body):
    return isinstance(body, dict) and set(body) >= {"filters", "limit", "offset", "sort"}


def resolve_body(op):
    content = {}
    body = op.get("requestBody") or {}
    if "$ref" in body:
        return "json", LIST_OPTIONS, "application/json"
    content = (body.get("content") or {})
    if "multipart/form-data" in content and "application/json" not in content:
        return "multipart", None, "multipart/form-data"
    if "application/json" in content or content:
        example = extract_curl_json(op)
        if is_list_options(example):
            example = LIST_OPTIONS
        return "json", example, "application/json"
    example = extract_curl_json(op)
    if example is not None:
        if is_list_options(example):
            example = LIST_OPTIONS
        return "json", example, "application/json"
    return "none", None, None


def parameters(op):
    found = []
    for param in op.get("parameters") or []:
        if param.get("name") == "Authorization":
            continue
        schema = param.get("schema") or {}
        found.append(
            {
                "name": param.get("name"),
                "in": param.get("in"),
                "required": bool(param.get("required")),
                "description": (param.get("description") or "")[:240],
                "type": schema.get("type") or "string",
            }
        )
    return found


def auth_for(op, path):
    if path == "/api/enterpriseai/v1/version":
        return "none"
    security = op.get("security") or []
    if any("BasicAuth" in item for item in security):
        return "basic"
    for param in op.get("parameters") or []:
        if param.get("name") == "Authorization":
            return "bearer"
    if path.startswith("/enterpriseai/"):
        return "bearer"
    if path.startswith("/api/"):
        return "basic"
    return "none"


def op_id(method, path):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", f"{method}-{path}").strip("-").lower()
    return slug


def build_operation(method, path, op, source="spec"):
    kind, example, content_type = resolve_body(op)
    key = (method.upper(), path)
    if key in OVERRIDES:
        example = OVERRIDES[key]
        kind = "json"
        content_type = content_type or "application/json"
    tag = (op.get("tags") or ["other"])[0]
    description = (op.get("description") or "").strip()
    summary = op.get("summary") or path
    return {
        "id": op_id(method, path),
        "method": method.upper(),
        "path": path,
        "tag": tag,
        "summary": summary,
        "description": description[:700],
        "auth": auth_for(op, path),
        "source": source,
        "bodyKind": kind,
        "contentType": content_type,
        "example": example,
        "parameters": parameters(op),
        "note": NOTES.get(key, ""),
    }


def group_for(item):
    key = (item["method"], item["path"])
    if key in DISCOVER:
        return "cluster"
    if item["path"].startswith("/enterpriseai/gateway/") or key == (
        "POST",
        "/enterpriseai/v2/rerank",
    ):
        return "gateway-call"
    if item["tag"] in GATEWAY_ADMIN_TAGS:
        return "gateway-admin"
    if item["tag"] in INFERENCE_TAGS:
        return "inference"
    if item["tag"] in MODEL_TAGS:
        return "models"
    if item["tag"] in OBSERVE_TAGS:
        return "observe"
    return "platform"


def main():
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    operations = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            operations.append(build_operation(method, path, op))

    clones = []
    by_path = {(item["method"], item["path"]): item for item in operations}
    for src, dest in GATEWAY_REWRITES.items():
        original = by_path.get(("POST", src)) or by_path.get(("GET", src))
        if not original:
            continue
        method = original["method"]
        cloned = json.loads(json.dumps(original))
        cloned["path"] = dest
        cloned["id"] = op_id(method, dest)
        cloned["source"] = "gateway-alias"
        cloned["auth"] = "bearer"
        override = OVERRIDES.get((method, dest))
        if override is not None:
            cloned["example"] = override
        cloned["note"] = NOTES.get((method, dest), cloned.get("note") or "")
        clones.append(cloned)

    # Messages and v2 rerank already come from the spec.
    operations.extend(clones)

    grouped = {key: [] for key, _, _ in GROUPS}
    for item in operations:
        grouped[group_for(item)].append(item)

    # Keep gateway aliases ahead of the messages operation.
    gateway_rank = {
        "/enterpriseai/gateway/v1/models": 0,
        "/enterpriseai/gateway/v1/chat/completions": 1,
        "/enterpriseai/gateway/v1/embeddings": 2,
        "/enterpriseai/gateway/v1/images/generations": 3,
        "/enterpriseai/gateway/v1/messages": 4,
        "/enterpriseai/v2/rerank": 5,
    }
    grouped["gateway-call"].sort(key=lambda item: gateway_rank.get(item["path"], 50))
    discover_rank = {
        "/api/enterpriseai/v1/version": 0,
        "/api/enterpriseai/v1/users/me": 1,
        "/api/enterpriseai/v1/cluster/health": 2,
        "/api/enterpriseai/v1/cluster/info": 3,
        "/api/enterpriseai/v1/cluster/config": 4,
    }
    grouped["cluster"].sort(key=lambda item: discover_rank.get(item["path"], 50))

    catalog = {
        "title": "Nutanix Enterprise AI",
        "version": spec["info"]["version"],
        "source": "https://www.nutanix.dev/api_reference/apis/nai2.8.html",
        "updated": "2026-08-20",
        "groups": [
            {"id": key, "title": title, "blurb": blurb, "operations": grouped[key]}
            for key, title, blurb in GROUPS
        ],
        "presets": [
            {
                "id": "mcp-tools-list",
                "title": "MCP tools/list",
                "method": "POST",
                "path": "",
                "auth": "bearer",
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
                "body": {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                "note": (
                    "Paste the connector access path from the NAI UI. "
                    "NAI 2.8 documents MCP connectors against specification 2025-06-18 "
                    "for tools/, prompts/, and resources/. This OpenAPI file does not "
                    "publish that protocol URL."
                ),
            },
            {
                "id": "mcp-tools-call",
                "title": "MCP tools/call",
                "method": "POST",
                "path": "",
                "auth": "bearer",
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "tool_name", "arguments": {}},
                },
                "note": "Same transport as tools/list. Set the tool name you allowed on the connector.",
            },
            {
                "id": "mcp-prompts-list",
                "title": "MCP prompts/list",
                "method": "POST",
                "path": "",
                "auth": "bearer",
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
                "body": {"jsonrpc": "2.0", "id": 3, "method": "prompts/list", "params": {}},
                "note": "MCP 2025-06-18 prompts/list template.",
            },
            {
                "id": "mcp-resources-list",
                "title": "MCP resources/list",
                "method": "POST",
                "path": "",
                "auth": "bearer",
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
                "body": {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "resources/list",
                    "params": {},
                },
                "note": "MCP 2025-06-18 resources/list template.",
            },
        ],
    }
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    total = sum(len(group["operations"]) for group in catalog["groups"])
    print(f"wrote {OUT_PATH} operations={total}")


if __name__ == "__main__":
    main()
