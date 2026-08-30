from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "deploy" / "fastgpt_agent_config.json"
DEFAULT_ENV_FILE = ROOT / ".env"


class FastGPTError(RuntimeError):
    pass


class FastGPTAdminClient:
    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def login(self, username: str, password: str) -> None:
        prelogin = self._request(
            "GET",
            "/api/support/user/account/preLogin",
            params={"username": username},
            authenticated=False,
        )
        code = str(prelogin.get("code", ""))
        if not code:
            raise FastGPTError("FastGPT pre-login did not return a verification code")

        password_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        login = self._request(
            "POST",
            "/api/support/user/account/loginByPassword",
            json={
                "username": username,
                "password": password_hash,
                "code": code,
                "language": "zh-CN",
            },
            authenticated=False,
        )
        if not login.get("token"):
            raise FastGPTError("FastGPT login succeeded without returning a session token")

    def list_apps(self, search_key: str) -> list[dict[str, Any]]:
        return self._request("POST", "/api/core/app/list", json={"searchKey": search_key})

    def create_app(self, payload: dict[str, Any]) -> str:
        return str(self._request("POST", "/api/core/app/create", json=payload))

    def update_app(self, app_id: str, payload: dict[str, Any]) -> None:
        self._request("PUT", "/api/core/app/update", params={"appId": app_id}, json=payload)

    def publish_app(
        self,
        app_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        chat_config: dict[str, Any],
        version_name: str,
    ) -> None:
        self._request(
            "POST",
            "/api/core/app/version/publish",
            params={"appId": app_id},
            json={
                "nodes": nodes,
                "edges": edges,
                "chatConfig": chat_config,
                "isPublish": True,
                "versionName": version_name,
                "autoSave": False,
            },
        )

    def create_http_toolset(self, name: str, intro: str) -> str:
        return str(
            self._request(
                "POST",
                "/api/core/app/httpTools/create",
                json={"name": name, "intro": intro, "createType": "batch"},
            )
        )

    def update_http_toolset(
        self,
        app_id: str,
        base_url: str,
        token: str,
        tools: list[dict[str, Any]],
    ) -> None:
        tool_list = []
        for tool in tools:
            input_schema = json.loads(json.dumps(tool["input_schema"]))
            for key, prop in input_schema.get("properties", {}).items():
                prop["x-tool-description"] = prop.get("description") or key
            item = {
                "name": tool["name"],
                "description": tool["description"],
                "path": tool["path"],
                "method": tool["method"],
                "requestSchema": input_schema,
                "inputSchema": input_schema,
                "outputSchema": tool["output_schema"],
            }
            if tool["method"].upper() in {"POST", "PUT", "PATCH"}:
                body = {key: f"{{{{{key}}}}}" for key in input_schema.get("properties", {})}
                item["staticBody"] = {
                    "type": "json",
                    "content": json.dumps(body, ensure_ascii=False),
                }
            tool_list.append(item)
        self._request(
            "PUT",
            "/api/core/app/httpTools/update",
            json={
                "appId": app_id,
                "baseUrl": base_url.rstrip("/"),
                "apiSchemaStr": "",
                "customHeaders": "{}",
                "headerSecret": {"Bearer": {"value": token, "secret": ""}},
                "toolList": tool_list,
            },
        )

    def get_tool_preview(self, app_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/core/app/tool/getPreviewNode",
            params={
                "appId": app_id,
                "getLatestVersion": "true",
            },
        )

    def validate_model(self, model_name: str) -> None:
        models = self._request("GET", "/api/core/ai/model/list")
        model = next((item for item in models if item.get("model") == model_name), None)
        if model is None:
            raise FastGPTError(f"FastGPT model not found: {model_name}")
        if not model.get("isActive"):
            raise FastGPTError(f"FastGPT model is not active: {model_name}")
        if not model.get("toolChoice"):
            raise FastGPTError(f"FastGPT model does not support tool calling: {model_name}")

    def get_or_create_api_key(self, name: str) -> str:
        keys = self._request(
            "GET",
            "/api/support/openapi/list",
            params={"keyword": name, "sortBy": "createTime"},
        )
        existing = next((item for item in keys if item.get("name") == name), None)
        if existing:
            return str(
                self._request(
                    "POST", "/api/support/openapi/copy", json={"id": existing["_id"]}
                )
            )
        return str(
            self._request(
                "POST",
                "/api/support/openapi/create",
                json={"name": name, "authProxy": False, "limit": {"maxUsagePoints": -1}},
            )
        )

    def _request(self, method: str, path: str, authenticated: bool = True, **kwargs: Any) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise FastGPTError(f"FastGPT request failed: {method} {path}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise FastGPTError(
                f"FastGPT returned non-JSON data: {method} {path} HTTP {response.status_code}"
            ) from exc

        if response.status_code >= 400 or payload.get("code") != 200:
            message = payload.get("message") or payload.get("statusText") or "unknown error"
            if authenticated and response.status_code in {401, 403}:
                message = f"authentication failed: {message}"
            raise FastGPTError(
                f"FastGPT API error: {method} {path} HTTP {response.status_code}: {message}"
            )
        return payload.get("data")


def build_workflow(
    config: dict[str, Any], tool_nodes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    app = config["app"]
    start_id = "smartLockWorkflowStart"
    chat_id = "smartLockAgentChat"
    nodes: list[dict[str, Any]] = [
        {
            "flowNodeType": "userGuide",
            "name": "系统配置",
            "intro": "",
            "nodeId": "userGuide",
            "inputs": [],
            "outputs": [],
            "position": {"x": 100, "y": 80},
        },
        {
            "flowNodeType": "workflowStart",
            "avatar": "core/workflow/template/workflowStart",
            "name": "流程开始",
            "intro": "",
            "nodeId": start_id,
            "inputs": [
                {
                    "key": "userChatInput",
                    "label": "用户问题",
                    "valueType": "string",
                    "required": True,
                    "renderTypeList": ["reference", "textarea"],
                    "toolDescription": "用户对智能门锁说的话",
                }
            ],
            "outputs": [
                {
                    "id": "userChatInput",
                    "key": "userChatInput",
                    "type": "static",
                    "valueType": "string",
                    "label": "用户问题",
                },
                {
                    "id": "userFiles",
                    "key": "userFiles",
                    "type": "static",
                    "valueType": "arrayString",
                    "label": "用户文件",
                },
            ],
            "position": {"x": 100, "y": 360},
        },
        build_agent_node(app, start_id, chat_id, tool_nodes),
    ]

    edges: list[dict[str, Any]] = [
        {
            "source": start_id,
            "sourceHandle": f"{start_id}-source-right",
            "target": chat_id,
            "targetHandle": f"{chat_id}-target-left",
        }
    ]
    chat_config = {
        "welcomeText": "您好，请说出您的来意。",
        "variables": [],
        "autoExecute": {"open": False, "defaultPrompt": ""},
        "questionGuide": {"open": False},
        "ttsConfig": {"type": "web"},
        "whisperConfig": {"open": False, "autoSend": False, "autoTTSResponse": False},
        "chatInputGuide": {"open": False, "customUrl": ""},
        "fileSelectConfig": {
            "maxFiles": 0,
            "canSelectFile": False,
            "canSelectImg": False,
            "canSelectVideo": False,
            "canSelectAudio": False,
            "canSelectCustomFileExtension": False,
            "customFileExtensionList": [],
        },
        "instruction": "",
    }
    return nodes, edges, chat_config


def build_agent_node(
    app: dict[str, Any],
    start_id: str,
    chat_id: str,
    tool_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_tools = [
        {
            "id": node["pluginId"],
            "config": {},
            **({"source": node["source"]} if node.get("source") else {}),
        }
        for node in tool_nodes
    ]
    return {
        "flowNodeType": "agent",
        "avatar": "core/workflow/template/agent",
        "name": "智能门锁 Agent",
        "intro": "DeepSeek 对话、意图识别与受控工具调用",
        "showStatus": True,
        "version": "1.0",
        "nodeId": chat_id,
        "inputs": [
            {
                **node_input("systemPrompt", "string", app["system_prompt"], ["textarea", "reference"]),
                "label": "系统提示词",
                "max": 8000,
            },
            {
                **node_input("history", "chatHistory", app["history_turns"], ["numberInput", "reference"]),
                "label": "对话历史",
                "required": True,
            },
            {
                **node_input(
                    "userChatInput", "string", [start_id, "userChatInput"], ["reference", "textarea"]
                ),
                "label": "用户问题",
                "required": True,
                "toolDescription": "用户对智能门锁说的话",
            },
            node_input("quoteQA", "datasetQuote", None, ["settingDatasetQuotePrompt"]),
            node_input("fileUrlList", "arrayString", [[start_id, "userFiles"]], ["reference", "input"]),
            node_input("aiChatVision", "boolean", False, ["hidden"]),
            node_input("aiChatAudio", "boolean", False, ["hidden"]),
            node_input("aiChatVideo", "boolean", False, ["hidden"]),
            node_input("aiChatExtractFiles", "boolean", False, ["hidden"]),
            node_input("aiChatReasoning", "boolean", bool(app.get("reasoning", False)), ["hidden"]),
            {
                **node_input("model", "string", app["model"], ["settingLLMModel", "reference"]),
                "label": "模型",
                "required": True,
            },
            {
                **node_input("agent_selectedTools", "any", selected_tools, ["selectTool"]),
                "label": "工具",
            },
        ],
        "outputs": [
            {
                "id": "history",
                "key": "history",
                "type": "static",
                "valueType": "chatHistory",
                "label": "新上下文",
                "required": True,
            },
            {
                "id": "answerText",
                "key": "answerText",
                "type": "static",
                "valueType": "string",
                "label": "AI 回复",
                "required": True,
            },
            {
                "id": "reasoningText",
                "key": "reasoningText",
                "type": "static",
                "valueType": "string",
                "label": "思考过程",
                "required": False,
                "invalid": True,
            },
            {
                "id": "system_error_text",
                "key": "system_error_text",
                "type": "error",
                "valueType": "string",
                "label": "系统错误",
            },
        ],
        "position": {"x": 500, "y": 280},
    }


def node_input(key: str, value_type: str, value: Any, render_types: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": key,
        "label": "",
        "valueType": value_type,
        "renderTypeList": render_types,
    }
    if value is not None:
        result["value"] = value
    return result


def exact_app(apps: list[dict[str, Any]], name: str, app_type: str) -> dict[str, Any] | None:
    matches = [item for item in apps if item.get("name") == name and item.get("type") == app_type]
    if len(matches) > 1:
        raise FastGPTError(f"multiple FastGPT apps named {name!r} with type {app_type!r}")
    return matches[0] if matches else None


def update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    managed = set(values)
    retained = [line for line in lines if line.split("=", 1)[0].strip() not in managed]
    if retained and retained[-1].strip():
        retained.append("")
    retained.append("# Managed by deploy/configure_fastgpt_agent.py")
    retained.extend(f"{key}={value}" for key, value in values.items())
    path.write_text("\n".join(retained) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update the FastGPT smart-lock Agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--fastgpt-url", default=os.getenv("FASTGPT_API_BASE", "http://127.0.0.1:3300"))
    parser.add_argument("--username", default=os.getenv("FASTGPT_ADMIN_USERNAME", "root"))
    parser.add_argument("--admin-password", default=os.getenv("FASTGPT_ADMIN_PASSWORD"))
    parser.add_argument(
        "--tool-base-url",
        default=os.getenv("JETSON_TOOL_BASE", "http://172.18.0.1:8787"),
        help="Jetson gateway URL as seen from the FastGPT container",
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    env_path = Path(args.env_file).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    existing_env = load_env_file(env_path)
    gateway_token = os.getenv("LOCK_TOOL_TOKEN") or existing_env.get("LOCK_TOOL_TOKEN")
    gateway_token = gateway_token or secrets.token_urlsafe(32)

    password = args.admin_password or getpass.getpass(f"FastGPT password for {args.username}: ")
    if not password:
        raise FastGPTError("FastGPT administrator password is required")

    client = FastGPTAdminClient(args.fastgpt_url, timeout=args.timeout)
    client.login(args.username, password)
    app_config = config["app"]
    toolset_config = config["http_toolset"]
    client.validate_model(app_config["model"])
    print(f"model ok: {app_config['model']} supports tool calling")

    toolsets = client.list_apps(toolset_config["name"])
    toolset = exact_app(toolsets, toolset_config["name"], "httpToolSet")
    if toolset:
        toolset_id = str(toolset["_id"])
        print(f"toolset update: {toolset_config['name']} ({toolset_id})")
    else:
        toolset_id = client.create_http_toolset(toolset_config["name"], toolset_config["intro"])
        print(f"toolset create: {toolset_config['name']} ({toolset_id})")
    client.update_http_toolset(toolset_id, args.tool_base_url, gateway_token, config["tools"])

    # Select the HTTP toolset as a whole. Selecting its child tools directly makes
    # FastGPT treat required request fields as fixed user configuration and omit
    # those tools at runtime instead of letting the LLM fill the arguments.
    tool_nodes = [client.get_tool_preview(toolset_id)]
    nodes, edges, chat_config = build_workflow(config, tool_nodes)

    apps = client.list_apps(app_config["name"])
    app = exact_app(apps, app_config["name"], app_config["type"])
    if app:
        app_id = str(app["_id"])
        client.update_app(
            app_id,
            {
                "name": app_config["name"],
                "intro": app_config["intro"],
                "nodes": nodes,
                "edges": edges,
                "chatConfig": chat_config,
            },
        )
        print(f"agent update: {app_config['name']} ({app_id})")
    else:
        app_id = client.create_app(
            {
                "name": app_config["name"],
                "intro": app_config["intro"],
                "type": app_config["type"],
                "modules": nodes,
                "edges": edges,
                "chatConfig": chat_config,
            }
        )
        print(f"agent create: {app_config['name']} ({app_id})")

    client.publish_app(
        app_id,
        nodes,
        edges,
        chat_config,
        app_config["version_name"],
    )
    api_key = client.get_or_create_api_key(config["api_key_name"])
    update_env_file(
        env_path,
        {
            "FASTGPT_API_BASE": args.fastgpt_url.rstrip("/"),
            "FASTGPT_APP_API_KEY": api_key,
            "FASTGPT_APP_ID": app_id,
            "JETSON_TOOL_BASE": args.tool_base_url.rstrip("/"),
            "LOCK_TOOL_TOKEN": gateway_token,
            "SMART_LOCK_NO_UNLOCK": "1",
            "LOCK_AUTH_CONTEXT_PATH": "logs/auth_context.json",
        },
    )
    print(f"published: {app_config['version_name']}")
    print(f"runtime env written: {env_path}")
    print(f"FASTGPT_APP_ID={app_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FastGPTError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2) from exc
