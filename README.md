# Codebase Knowledge Extractor

This is my submission for the Polyglot Developer assignment. The tool analyzes a Java/Spring codebase and generates a structured `knowledge.json` file that can be used to understand the project quickly.

The current target codebase is `codejsha/spring-rest-sakila`, a Spring Boot REST API around the Sakila sample database. The analyzer combines deterministic local parsing with optional LLM summaries so the output contains both reliable code facts and higher-level interpretation.

## What It Produces

`knowledge.json` includes:

- repository metadata and analysis settings
- project overview, technology stack, and key source areas
- discovered classes and annotations
- discovered Spring API mapping annotations
- Java method signatures with line ranges
- short method descriptions
- approximate cyclomatic complexity
- optional LLM-generated summaries for selected code chunks

## Setup

Create and activate a virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `py -3` is not available on your machine, use the full path to your Python 3 executable.

## Run The Analysis

From this folder:

```powershell
py -3 src\analyze_codebase.py --output knowledge.json
```

The script clones the Sakila repository into the system temp folder if it is not already present.

To analyze a local checkout instead:

```powershell
py -3 src\analyze_codebase.py --repo-path C:\path\to\spring-rest-sakila --output knowledge.json
```

## Connect The LLM

LLM summaries are optional. The analyzer still produces a complete static `knowledge.json` without an API key.

If you want to run the LLM pass, do not hard-code the API key in source files. Set it in your terminal for the current session:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

Or create a local `.env` file:

```text
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

`.env` is ignored by git so the key is not submitted by accident.

Test the connection:

```powershell
py -3 src\analyze_codebase.py --test-llm
```

Expected output:

```text
LLM connection ok
```

Run the full analysis with LLM summaries:

```powershell
py -3 src\analyze_codebase.py --use-llm --llm-chunk-limit 6 --output knowledge.json
```

The default model is `gpt-4o-mini`. You can change it:

```powershell
py -3 src\analyze_codebase.py --use-llm --model gpt-4o-mini --llm-chunk-limit 10 --output knowledge.json
```

## Design Notes

The deterministic pass extracts repeatable facts locally: files, classes, annotations, endpoint mappings, method signatures, line ranges, and rough complexity. This keeps the output useful even when no LLM key is configured.

The optional LLM pass sends chunked source text to OpenAI and asks for compact JSON summaries. Chunking keeps prompts under a predictable size and the `--llm-chunk-limit` flag keeps cost and runtime controlled.

The implementation intentionally uses lightweight Python and a small dependency set. For a production-grade analyzer, I would replace the regex Java parsing with JavaParser, Spoon, or tree-sitter.

## Submission Files

- `src/analyze_codebase.py`: main analyzer
- `requirements.txt`: Python dependencies
- `knowledge.json`: generated analysis output
