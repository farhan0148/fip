"""Guards that keep the calculator deterministic and AI code inside app/ai/.

The Node run at the bottom executes the browser calculator against the same
hand-worked cases as Python, so the two cannot drift apart.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

PROVIDER_WORDS = re.compile(r"groq|openai|anthropic|gemini|mistral|cohere|huggingface|llm|api\.groq", re.IGNORECASE)
NETWORK_JS = re.compile(
    r"\bfetch\s*\(|XMLHttpRequest|\bWebSocket\b|EventSource|sendBeacon|importScripts|\brequire\s*\(|"
    r"\bimport\s*[\w{(*]|https?://|\bwindow\.location|\bdocument\.",
    re.IGNORECASE,
)


def test_python_calculator_imports_nothing_but_re():
    tree = ast.parse((APP / "simulator.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0] or "relative-import")
    assert imported == {"re"}, f"app/simulator.py may only import re, found {sorted(imported)}"


def test_python_calculator_has_no_network_or_io_calls():
    source = (APP / "simulator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & {"open", "exec", "eval", "__import__", "urlopen", "post", "request", "connect", "send", "fetch", "read_text"}), called
    assert not PROVIDER_WORDS.search(source)
    assert "http" not in source.lower()


def test_js_calculator_has_no_network_calls_imports_or_dom_access():
    source = (APP / "static" / "js" / "simulator-core.js").read_text(encoding="utf-8")
    assert not NETWORK_JS.search(source), NETWORK_JS.search(source).group(0)
    assert not PROVIDER_WORDS.search(source)


def test_ai_provider_code_lives_only_in_app_ai():
    offenders = []
    for path in list(APP.rglob("*.py")) + list(APP.rglob("*.js")):
        relative = path.relative_to(APP)
        if relative.parts[0] == "ai":
            continue
        text = path.read_text(encoding="utf-8")
        if PROVIDER_WORDS.search(text) or "GROQ_" in text:
            offenders.append(str(relative))
    assert offenders == [], f"AI provider references outside app/ai: {offenders}"


def test_only_app_ai_reads_the_provider_key():
    for path in APP.rglob("*.py"):
        if path.relative_to(APP).parts[0] == "ai":
            continue
        assert "os.environ" not in path.read_text(encoding="utf-8") or path.name == "__init__.py", path


def test_js_calculator_matches_python_on_the_shared_cases():
    node = shutil.which("node")
    if node is None:
        pytest.fail(
            "Node.js is needed to check the browser calculator against the Python one. "
            "Install Node 20 or later, or deselect with: -k 'not js_calculator'"
        )
    run = subprocess.run([node, "--test", "tests/js"], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert run.returncode == 0, (run.stdout[-3000:] + run.stderr[-1500:])
