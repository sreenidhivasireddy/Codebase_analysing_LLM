import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_REPO = "https://github.com/codejsha/spring-rest-sakila.git"
CODE_EXTENSIONS = {".java", ".xml", ".yml", ".yaml", ".properties", ".gradle", ".md"}
SKIP_DIRS = {".git", "target", "build", ".gradle", ".idea", ".mvn/wrapper"}
DEFAULT_MODEL = "gpt-4o-mini"
ENDPOINT_RE = re.compile(
    r"@(?P<annotation>GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\s*"
    r"\((?P<args>[^)]*)\)",
    re.MULTILINE,
)
CLASS_RE = re.compile(
    r"(?P<annotations>(?:@\w+(?:\([^)]*\))?\s*)*)"
    r"(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(?P<name>\w+)",
    re.MULTILINE,
)


@dataclass
class MethodInfo:
    file: str
    signature: str
    name: str
    start_line: int
    end_line: int
    description: str
    cyclomatic_complexity: int


@dataclass
class EndpointInfo:
    file: str
    annotation: str
    path: str
    line: int


@dataclass
class ClassInfo:
    file: str
    name: str
    annotations: list[str]
    line: int


def ensure_repo(repo_url: str, target: Path) -> Path:
    if target.exists() and any(target.iterdir()):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth", "1", repo_url, str(target)], check=True)
    return target


def should_read(path: Path) -> bool:
    parts = set(path.parts)
    return path.suffix.lower() in CODE_EXTENSIONS and not (parts & SKIP_DIRS)


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_file() and should_read(path):
            yield path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def load_env_file(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(path)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chunk_files(root: Path, max_tokens: int = 3500) -> list[dict]:
    chunks = []
    current = []
    current_tokens = 0
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        block = f"\n\n--- FILE: {rel} ---\n{text}"
        tokens = estimate_tokens(block)
        if current and current_tokens + tokens > max_tokens:
            joined = "".join(current)
            chunks.append({
                "id": hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12],
                "estimated_tokens": current_tokens,
                "content": joined,
            })
            current, current_tokens = [], 0
        current.append(block)
        current_tokens += tokens
    if current:
        joined = "".join(current)
        chunks.append({
            "id": hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12],
            "estimated_tokens": current_tokens,
            "content": joined,
        })
    return chunks


METHOD_RE = re.compile(
    r"(?P<signature>(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], ?]+\s+(?P<name>\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w, ]+)?\s*)\{",
    re.MULTILINE,
)


def find_matching_brace(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def complexity(body: str) -> int:
    decisions = re.findall(r"\b(if|for|while|case|catch)\b|&&|\|\||\?", body)
    return 1 + len(decisions)


def describe_method(name: str, signature: str) -> str:
    readable = re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower()
    if name.startswith("get"):
        return f"Retrieves {readable[4:] or 'data'}."
    if name.startswith("find"):
        return f"Finds {readable[5:] or 'matching records'}."
    if name.startswith("save") or name.startswith("create"):
        return f"Creates or persists {readable.split(' ', 1)[-1]}."
    if name.startswith("delete") or name.startswith("remove"):
        return f"Deletes or removes {readable.split(' ', 1)[-1]}."
    return f"Implements {readable}."


def extract_methods(root: Path) -> list[MethodInfo]:
    methods = []
    for path in iter_files(root):
        if path.suffix != ".java":
            continue
        text = read_text(path)
        for match in METHOD_RE.finditer(text):
            open_brace = text.find("{", match.start())
            close_brace = find_matching_brace(text, open_brace)
            body = text[open_brace:close_brace + 1]
            methods.append(MethodInfo(
                file=path.relative_to(root).as_posix(),
                signature=" ".join(match.group("signature").split()),
                name=match.group("name"),
                start_line=line_number(text, match.start()),
                end_line=line_number(text, close_brace),
                description=describe_method(match.group("name"), match.group("signature")),
                cyclomatic_complexity=complexity(body),
            ))
    return methods


def clean_mapping_path(raw_args: str) -> str:
    quoted = re.search(r'"([^"]*)"', raw_args)
    if quoted:
        return quoted.group(1) or "/"
    named = re.search(r"(?:path|value)\s*=\s*\{?\s*\"([^\"]*)\"", raw_args)
    if named:
        return named.group(1) or "/"
    return "/"


def extract_endpoints(root: Path) -> list[EndpointInfo]:
    endpoints = []
    for path in iter_files(root):
        if path.suffix != ".java":
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        for match in ENDPOINT_RE.finditer(text):
            endpoints.append(EndpointInfo(
                file=rel,
                annotation=match.group("annotation"),
                path=clean_mapping_path(match.group("args")),
                line=line_number(text, match.start()),
            ))
    return endpoints


def extract_classes(root: Path) -> list[ClassInfo]:
    classes = []
    for path in iter_files(root):
        if path.suffix != ".java":
            continue
        rel = path.relative_to(root).as_posix()
        text = read_text(path)
        for match in CLASS_RE.finditer(text):
            annotations = re.findall(r"@(\w+)", match.group("annotations") or "")
            classes.append(ClassInfo(
                file=rel,
                name=match.group("name"),
                annotations=annotations,
                line=line_number(text, match.start()),
            ))
    return classes


def summarize_project(root: Path, methods: list[MethodInfo], endpoints: list[EndpointInfo], classes: list[ClassInfo]) -> dict:
    files = [p.relative_to(root).as_posix() for p in iter_files(root)]
    java_files = [f for f in files if f.endswith(".java")]
    controllers = [m.file for m in methods if "controller" in m.file.lower()]
    repositories = [f for f in java_files if "repository" in f.lower()]
    entities = [f for f in java_files if "/model/" in f.lower() or "/entity/" in f.lower()]
    return {
        "name": root.name,
        "purpose": "Spring Boot REST API for accessing and exposing Sakila sample database resources.",
        "technology_stack": ["Java", "Spring Boot", "Spring Data JPA", "Spring HATEOAS", "QueryDSL", "Maven/Gradle"],
        "file_count": len(files),
        "java_file_count": len(java_files),
        "key_areas": {
            "controllers": sorted(set(controllers)),
            "repositories": sorted(set(repositories)),
            "domain_entities": sorted(set(entities)),
        },
        "endpoint_count": len(endpoints),
        "class_count": len(classes),
    }


def build_chunk_prompt(chunk: dict) -> str:
    return (
        "You are helping analyze a Java Spring Boot project. "
        "Return compact JSON with these keys: responsibilities, important_classes, "
        "api_endpoints, and risks_or_notes. Keep it brief and factual.\n\n"
        + chunk["content"][:12000]
    )


def create_openai_client():
    load_env_file()
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed. Run: pip install openai") from exc
    return OpenAI()


def test_llm_connection(model: str) -> str:
    client = create_openai_client()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: LLM connection ok"}],
            temperature=0,
            max_tokens=10,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc
    return response.choices[0].message.content.strip()


def call_llm(chunks: list[dict], model: str, limit: int) -> list[dict]:
    client = create_openai_client()
    results = []
    for chunk in chunks[:limit]:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": build_chunk_prompt(chunk)}],
                temperature=0,
            )
        except Exception as exc:
            raise RuntimeError(f"OpenAI request failed for chunk {chunk['id']}: {exc}") from exc
        content = response.choices[0].message.content.strip()
        try:
            summary = json.loads(content)
        except json.JSONDecodeError:
            summary = {"raw_summary": content}
        results.append({"chunk_id": chunk["id"], "summary": summary})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract navigable knowledge from a Java/Spring codebase.")
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--repo-path", default="")
    parser.add_argument("--output", default="knowledge.json")
    parser.add_argument("--max-tokens", type=int, default=3500)
    parser.add_argument("--use-llm", action="store_true", help="Send code chunks to OpenAI after static analysis.")
    parser.add_argument("--test-llm", action="store_true", help="Only test the OpenAI connection and exit.")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--llm-chunk-limit", type=int, default=6, help="Limits LLM calls to keep cost and runtime controlled.")
    args = parser.parse_args()
    load_env_file()

    if args.test_llm:
        try:
            print(test_llm_connection(args.model))
        except RuntimeError as exc:
            print(f"LLM connection not ready: {exc}")
            raise SystemExit(1)
        return

    if args.repo_path:
        repo = Path(args.repo_path).resolve()
    else:
        repo = Path(tempfile.gettempdir()) / "spring-rest-sakila"
        ensure_repo(args.repo_url, repo)

    chunks = chunk_files(repo, args.max_tokens)
    methods = extract_methods(repo)
    endpoints = extract_endpoints(repo)
    classes = extract_classes(repo)
    llm_summaries = []
    llm_status = "not_requested"
    if args.use_llm:
        try:
            llm_summaries = call_llm(chunks, args.model, args.llm_chunk_limit)
            llm_status = "completed"
        except RuntimeError as exc:
            llm_status = f"skipped: {exc}"

    output = {
        "repository": args.repo_url,
        "analysis_strategy": {
            "included_extensions": sorted(CODE_EXTENSIONS),
            "max_tokens_per_chunk": args.max_tokens,
            "chunk_count": len(chunks),
            "token_estimation": "Approximate: 1 token per 4 characters.",
            "llm_model": args.model,
            "llm_status": llm_status,
            "llm_chunk_limit": args.llm_chunk_limit if args.use_llm else 0,
        },
        "project_overview": summarize_project(repo, methods, endpoints, classes),
        "classes": [asdict(item) for item in classes],
        "api_endpoints": [asdict(endpoint) for endpoint in endpoints],
        "methods": [asdict(method) for method in methods],
        "complexity_summary": {
            "method_count": len(methods),
            "highest_complexity_methods": [
                asdict(method) for method in sorted(methods, key=lambda m: m.cyclomatic_complexity, reverse=True)[:10]
            ],
        },
        "llm_chunk_summaries": llm_summaries,
        "limitations": [
            "Regex method extraction is intentionally lightweight; JavaParser would improve accuracy for complex syntax.",
            "Local complexity is approximate cyclomatic complexity based on control-flow keywords.",
            "LLM summaries require OPENAI_API_KEY and the openai Python package.",
        ],
    }
    Path(args.output).write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
