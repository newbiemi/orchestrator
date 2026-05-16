# Repository Map

Root: orchestrator

├── config
    └── pipelines.yaml
├── context
    ├── memory.db  [binary]
    ├── runs.db  [binary]
    ├── session_log.md
    └── summary.md
├── core
    ├── logs
        └── optimizer.db  [binary]
    ├── __init__.py
    ├── claude_node.py
    ├── config.py
    ├── logger.py
    ├── memory.py
    ├── ollama_node.py
    ├── pipeline.py
    └── prompt_optimizer.py
├── memory-bank
    ├── activeContext.md
    ├── planLog.md
    ├── progress.md
    ├── projectbrief.md
    └── systemPatterns.md
├── pipelines
    ├── __init__.py
    ├── context_summarizer.py
    ├── px_dashboard.py
    └── trading.py
├── scripts
    └── auto_summarize_all.py
├── api.py
├── GUIDE.md
├── HOW_TO_USE.md
├── requirements.txt
└── runner.py
