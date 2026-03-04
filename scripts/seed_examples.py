"""
Seed script to create example optimization records for development/demo.

Creates sample completed optimization records across various task types
so the history view and UI have data to display.

Usage:
    cd backend && python -m scripts.seed_examples
    # or from project root:
    PYTHONPATH=backend python scripts/seed_examples.py
"""
import asyncio
import json
import sys
import os
import uuid
from datetime import datetime, timezone, timedelta

# Ensure the backend app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.database import create_tables, async_session
from app.models.optimization import Optimization


EXAMPLE_OPTIMIZATIONS = [
    {
        "raw_prompt": "Write me a Python function that sorts a list",
        "optimized_prompt": (
            "# Role\n"
            "You are an expert Python developer specializing in algorithms and clean code.\n\n"
            "# Task\n"
            "Write a Python function that sorts a list of elements.\n\n"
            "# Requirements\n"
            "- Function signature: `def sort_list(items: list, reverse: bool = False) -> list`\n"
            "- Support both ascending and descending order via the `reverse` parameter\n"
            "- Handle edge cases: empty list, single element, already sorted\n"
            "- Include type hints and a docstring\n"
            "- Use Python's built-in `sorted()` for production code, but also provide\n"
            "  a manual implementation (e.g., merge sort) for educational purposes\n\n"
            "# Output Format\n"
            "Return the code as a single Python module with both implementations and\n"
            "example usage in an `if __name__ == '__main__'` block."
        ),
        "task_type": "coding",
        "complexity": "simple",
        "weaknesses": ["No constraints specified", "No output format", "Missing edge case handling"],
        "strengths": ["Clear intent"],
        "primary_framework": "structured-output",
        "framework_applied": "structured-output",
        "strategy_rationale": "Coding tasks benefit from strict output format specifications.",
        "clarity_score": 9,
        "specificity_score": 8,
        "structure_score": 9,
        "faithfulness_score": 9,
        "conciseness_score": 8,
        "overall_score": 9,
        "is_improvement": True,
        "verdict": "Significant improvement with clear structure and specific requirements.",
        "duration_ms": 4200,
    },
    {
        "raw_prompt": "Explain how transformers work in machine learning",
        "optimized_prompt": (
            "# Context\n"
            "You are explaining transformer architecture to a software engineer with basic ML knowledge "
            "but no prior exposure to attention mechanisms.\n\n"
            "# Objective\n"
            "Provide a clear, technical explanation of the transformer architecture as introduced in "
            "\"Attention Is All You Need\" (Vaswani et al., 2017).\n\n"
            "# Style & Tone\n"
            "Technical but accessible. Use analogies where they aid understanding. Avoid jargon "
            "without first defining it.\n\n"
            "# Structure\n"
            "1. The problem transformers solve (limitations of RNNs/LSTMs)\n"
            "2. Self-attention mechanism (with a worked example)\n"
            "3. Multi-head attention\n"
            "4. Positional encoding\n"
            "5. Encoder-decoder architecture\n"
            "6. Why transformers dominate modern NLP and beyond\n\n"
            "# Response Format\n"
            "Markdown with section headers. Include at least one diagram description "
            "(as ASCII art or a described figure). Total length: 800-1200 words."
        ),
        "task_type": "education",
        "complexity": "moderate",
        "weaknesses": ["No audience specified", "No depth level", "No structure"],
        "strengths": ["Clear topic", "Specific domain"],
        "primary_framework": "CO-STAR",
        "framework_applied": "CO-STAR",
        "strategy_rationale": "Educational content benefits from CO-STAR for audience-aware structuring.",
        "clarity_score": 9,
        "specificity_score": 9,
        "structure_score": 10,
        "faithfulness_score": 8,
        "conciseness_score": 7,
        "overall_score": 9,
        "is_improvement": True,
        "verdict": "Excellent improvement with clear pedagogical structure and audience targeting.",
        "duration_ms": 6800,
    },
    {
        "raw_prompt": "Help me analyze this dataset of customer transactions",
        "optimized_prompt": (
            "# Role\n"
            "You are a senior data analyst specializing in e-commerce transaction analysis.\n\n"
            "# Task\n"
            "Analyze a dataset of customer transactions to identify key patterns, anomalies, "
            "and actionable business insights.\n\n"
            "# Step-by-Step Approach\n"
            "1. **Data Overview**: Summarize the dataset structure (columns, data types, row count, "
            "   date range, missing values)\n"
            "2. **Descriptive Statistics**: Compute key metrics (total revenue, average order value, "
            "   transaction frequency, customer count)\n"
            "3. **Temporal Analysis**: Identify trends over time (daily/weekly/monthly patterns, "
            "   seasonality, growth rate)\n"
            "4. **Customer Segmentation**: Group customers by behavior (RFM analysis or similar)\n"
            "5. **Anomaly Detection**: Flag unusual transactions (outliers in amount, frequency, or timing)\n"
            "6. **Recommendations**: Provide 3-5 actionable business recommendations based on findings\n\n"
            "# Output Format\n"
            "Return analysis as structured Markdown with:\n"
            "- Summary table for each section\n"
            "- Key findings highlighted in bold\n"
            "- Python code snippets (pandas/matplotlib) for reproducibility"
        ),
        "task_type": "analysis",
        "complexity": "complex",
        "weaknesses": ["No dataset description", "No specific questions", "No output format"],
        "strengths": ["Clear general intent"],
        "primary_framework": "chain-of-thought",
        "framework_applied": "chain-of-thought",
        "strategy_rationale": "Analysis tasks require step-by-step reasoning with context enrichment.",
        "clarity_score": 8,
        "specificity_score": 8,
        "structure_score": 9,
        "faithfulness_score": 7,
        "conciseness_score": 7,
        "overall_score": 8,
        "is_improvement": True,
        "verdict": "Good improvement with structured analytical approach.",
        "duration_ms": 5500,
    },
]


async def seed():
    """Create sample optimization records."""
    await create_tables()

    async with async_session() as session:
        # Check if records already exist
        from sqlalchemy import select, func
        count_result = await session.execute(
            select(func.count(Optimization.id))
        )
        existing_count = count_result.scalar() or 0

        if existing_count > 0:
            print(f"Database already has {existing_count} optimization(s). Skipping seed.")
            return

        now = datetime.now(timezone.utc)
        for i, example in enumerate(EXAMPLE_OPTIMIZATIONS):
            opt = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=example["raw_prompt"],
                optimized_prompt=example["optimized_prompt"],
                task_type=example["task_type"],
                complexity=example["complexity"],
                weaknesses=json.dumps(example["weaknesses"]),
                strengths=json.dumps(example["strengths"]),
                primary_framework=example["primary_framework"],
                framework_applied=example["framework_applied"],
                strategy_rationale=example["strategy_rationale"],
                changes_made=json.dumps(["Added structure", "Added constraints", "Specified output format"]),
                optimization_notes=f"Applied {example['framework_applied']} framework.",
                clarity_score=example["clarity_score"],
                specificity_score=example["specificity_score"],
                structure_score=example["structure_score"],
                faithfulness_score=example["faithfulness_score"],
                conciseness_score=example["conciseness_score"],
                overall_score=example["overall_score"],
                is_improvement=example["is_improvement"],
                verdict=example["verdict"],
                issues=json.dumps([]),
                duration_ms=example["duration_ms"],
                provider_used="anthropic_api",
                model_analyze="claude-haiku-4-5-20251001",
                model_strategy="claude-opus-4-6",
                model_optimize="claude-opus-4-6",
                model_validate="claude-sonnet-4-6",
                status="completed",
                tags=json.dumps([]),
                created_at=now - timedelta(hours=len(EXAMPLE_OPTIMIZATIONS) - i),
            )
            session.add(opt)

        await session.commit()
        print(f"Seeded {len(EXAMPLE_OPTIMIZATIONS)} example optimization records.")


if __name__ == "__main__":
    asyncio.run(seed())
