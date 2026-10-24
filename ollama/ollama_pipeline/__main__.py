"""`python -m ollama_pipeline` - checks every host and configured model."""

import asyncio

from . import Registry, load_config
from .config import config_dir


async def main() -> int:
    registry = Registry(load_config())
    try:
        print(f"config     {config_dir()}")
        health = await registry.health()
        # A host that only serves the workspaces' embedding model is optional: without it,
        # workspaces search by words. It does not make this check fail.
        used = {m.host for m in registry.config.models}
        optional = {registry.config.workspaces.embed_host} - used
        for name, info in health.items():
            server = "" if registry.kind(name) == "llamacpp" else "Ollama "  # llama-server's version names itself
            state = f"ok ({server}{info['version']})" if info["ok"] else "UNREACHABLE"
            if name in optional:
                state += "  (embeddings for workspaces, optional)"
            print(f"host {name:<10} {info['url']:<32} {state}")

        missing = []
        print()
        for entry in await registry.list_models():
            cloud = entry.host not in health  # an OpenCode model: nothing to pull, no Ollama host
            served = not cloud and registry.kind(entry.host) == "llamacpp"
            state = "ready" if entry.available else "not available" if cloud else "not served" if served else "not pulled"
            if entry.available and entry.tools:
                state += ", calls tools"
            print(f"  {entry.id:<28} {entry.model:<24} {entry.host:<8} {state}")
            if not entry.available and not cloud and not served and health[entry.host]["ok"]:
                missing.append(entry.model)

        if missing:
            print("\nPull the missing models with:")
            for model in dict.fromkeys(missing):
                print(f"  ollama pull {model}")
        return 0 if all(info["ok"] for name, info in health.items() if name not in optional) else 1
    finally:
        await registry.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
