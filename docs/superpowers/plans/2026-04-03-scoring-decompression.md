# Scoring System Decompression — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompress the scoring system from a 5.7-7.8 band to the full 1-10 scale by recalibrating the LLM rubric, heuristic scorer, dimension weights, and adding improvement_score.

**Architecture:** 7 changes across scoring rubric (prompt), blender (weights + z-score), heuristic scorer (baselines), data model (new column), pipeline (computation), and taxonomy (centroid formula). Each change is independent and testable.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-03-scoring-decompression-design.md`

---

### Task 1: Rewrite LLM Scoring Rubric

**Files:**
- Modify: `prompts/scoring.md`

- [ ] **Step 1: Rewrite the conciseness dimension**

Replace the conciseness rubric section (lines 72-84 of `prompts/scoring.md`) with task-relative conciseness:

```xml
  <dimension name="conciseness">
    <description>Is the prompt appropriately sized for its task complexity? Score information density — every sentence should earn its place. A 600-word structured system design prompt and a 50-word classification prompt can both score 8+ if neither has unnecessary content. Penalize filler, repetition, and over-specification — not length itself.</description>
    <score value="1-2">Extremely verbose. Most content is filler, repetition, or tangential elaboration.</score>
    <score value="3-4">Noticeably padded. Multiple sentences could be removed without losing information.</score>
    <score value="5-6">Some filler or redundancy present. A few sentences don't contribute new information.</score>
    <score value="7-8">High information density. Almost every sentence contributes unique value relative to the task's complexity.</score>
    <score value="9-10">Maximally dense for its task. Cannot remove a sentence without losing essential information or structure.</score>
    <calibration-example score="3">I would like you to please write me a function, if you could, that would take in a list of numbers and then go through each number and add them all up together to get the total sum of all the numbers in the list</calibration-example>
    <calibration-example score="5">Write a Python function that takes a list of numbers as input and returns their sum. The function should handle empty lists by returning 0. Make sure to include type hints for the function parameters and return value.</calibration-example>
    <calibration-example score="7">## Task\nWrite sum_list(numbers: list[float]) -> float.\n\n## Requirements\n- Return sum of elements\n- Empty list → 0.0\n- Include docstring\n\n## Output\nPython function with type hints.</calibration-example>
    <calibration-example score="8">A 500-word structured prompt for designing a microservices architecture with 5 headed sections (Context, Services, Constraints, Output Format, Examples) where every section adds unique requirements — high density despite length.</calibration-example>
    <calibration-example score="10">sum_list(numbers: list[float]) -> float. Sum of elements. Empty → 0.0.</calibration-example>
    <score-distinction value="5-vs-7">A 5 has content that could be cut without losing meaning. A 7 has no obvious cuts — its length matches the task's inherent complexity.</score-distinction>
    <score-distinction value="7-vs-9">A 7 is appropriately sized. A 9 achieves the same precision with noticeably fewer words, or covers a complex task with no wasted structure.</score-distinction>
  </dimension>
```

- [ ] **Step 2: Rewrite the faithfulness dimension**

Replace the faithfulness rubric section (lines 57-70) with intent-preservation framing:

```xml
  <dimension name="faithfulness">
    <description>Does the prompt preserve and serve the user's core intent? Adding relevant constraints, structure, and examples that help achieve the user's goal is NOT scope creep — it's faithful enhancement. Only penalize additions that change WHAT the user is trying to accomplish.</description>
    <score value="1-2">Intent completely lost or contradicted. The prompt asks for something different.</score>
    <score value="3-4">Core intent present but significant aspects altered, omitted, or overshadowed by additions.</score>
    <score value="5-6">Intent preserved but notable scope drift — additions are tangential rather than serving the original goal.</score>
    <score value="7-8">Intent fully preserved. Additions (constraints, examples, structure) directly serve the original goal.</score>
    <score value="9-10">Perfect intent service. Every addition makes the original goal more achievable. Nothing distracts from the core ask.</score>
    <calibration-example score="3">Original asked for a REST API; rewrite focuses on a CLI tool instead</calibration-example>
    <calibration-example score="5">Original asked to "summarize meeting notes"; rewrite shifts to "extract action items with owners and deadlines" — related but different task</calibration-example>
    <calibration-example score="7">Original asked to "validate emails"; rewrite adds input sanitization and error types — useful additions that serve the validation goal</calibration-example>
    <calibration-example score="9">Original asked for a sort function; rewrite adds type hints, edge cases, and a docstring example — same function, enhanced for production use</calibration-example>
    <calibration-example score="10">Original asked for "a Python CSV parser that computes averages"; rewrite specifies function signature, return type, non-numeric column handling, and an example — identical goal with precision that ensures correct implementation</calibration-example>
    <score-distinction value="5-vs-7">A 5 adds things the user didn't ask for that change the task's direction. A 7 adds things the user would have asked for if they'd thought of them.</score-distinction>
    <score-distinction value="7-vs-9">A 7 has useful additions alongside the core ask. A 9's additions are so well-targeted that removing any of them would make the prompt less likely to achieve the user's original goal.</score-distinction>
  </dimension>
```

- [ ] **Step 3: Add anti-compression directive to evaluation instructions**

Replace lines 131-133 of `prompts/scoring.md` (the current range instructions) with:

```markdown
4. **Use the FULL 1-10 range.** A vague one-line prompt should score 2-4 on specificity, not 5-6. A well-structured prompt with concrete examples should score 8-9 on structure, not 7. If all your scores for a prompt fall between 6 and 8, you are compressing the scale — re-examine using the calibration examples.
5. Length is NOT a flaw. A 500-word prompt that needs every word scores 8+ on conciseness. A 50-word prompt with filler scores 4. Judge information density relative to the task's complexity, not absolute word count.
6. Faithfulness rewards intent-serving additions. A rewrite that adds structure, constraints, and examples to serve the user's original goal scores 8-9, not 5-6. Only penalize additions that change WHAT the user wants.
```

- [ ] **Step 4: Commit**

```bash
git add prompts/scoring.md
git commit -m "feat: rewrite scoring rubric — task-relative conciseness, intent faithfulness, anti-compression"
```

---

### Task 2: Weighted Dimension Overall + Disable Z-Score

**Files:**
- Modify: `backend/app/services/score_blender.py`

- [ ] **Step 1: Add dimension weights constant**

After the `HEURISTIC_WEIGHTS` dict (line 25), add:

```python
# Dimension weights for overall score computation.
# Conciseness is downweighted because prompt optimization inherently adds
# structure and detail — penalizing length with equal weight compresses
# all scores into a narrow band around 7.0.
DIMENSION_WEIGHTS: dict[str, float] = {
    "clarity": 0.25,
    "specificity": 0.25,
    "structure": 0.20,
    "faithfulness": 0.20,
    "conciseness": 0.10,
}
```

- [ ] **Step 2: Replace arithmetic mean with weighted mean**

Replace line 158-159 in `blend_scores()`:

```python
    # Overall: arithmetic mean of blended scores
    overall = round(sum(blended.values()) / len(blended), 2)
```

With:

```python
    # Overall: weighted mean — conciseness downweighted to prevent compression
    overall = round(
        sum(blended[dim] * DIMENSION_WEIGHTS[dim] for dim in DIMENSIONS),
        2,
    )
```

- [ ] **Step 3: Disable z-score normalization**

Change `ZSCORE_MIN_SAMPLES` from 10 to 999999 (line 30):

```python
ZSCORE_MIN_SAMPLES = 999999   # Disabled — re-enable after rubric recalibration baseline
```

- [ ] **Step 4: Run tests**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v -k "score" 2>&1 | tail -20`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/score_blender.py
git commit -m "feat: weighted dimension overall + disable z-score normalization"
```

---

### Task 3: Recalibrate Heuristic Scorer

**Files:**
- Modify: `backend/app/services/heuristic_scorer.py`

- [ ] **Step 1: Raise structure baseline and bonuses**

In `heuristic_structure()` (line 63), change baseline from 3.0 to 4.0 and increase bonuses:

```python
        score = 4.0  # Raised from 3.0 — well-structured prompts should reach 8.5+

        headers = re.findall(r"(?m)^#{1,6}\s+\S", prompt)
        n_headers = len(headers)
        if n_headers >= 3:
            score += 2.5  # Multiple sections = excellent structure
        elif n_headers >= 1:
            score += 1.5

        list_items = re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt)
        n_items = len(list_items)
        if n_items >= 4:
            score += 2.0  # Detailed lists
        elif n_items >= 2:
            score += 1.5
        elif n_items == 1:
            score += 0.5
```

- [ ] **Step 2: Raise clarity ceiling**

In `heuristic_clarity()` (line 198), widen the Flesch mapping range from 3-8 to 3-9:

```python
        # Map Flesch score (0-100) to our scale (3-9) — raised ceiling from 8
        score = 3.0 + (flesch / 100.0) * 6.0
```

Also increase structural clarity bonus (line 203):

```python
        if re.search(r"(?m)^#{1,3}\s+\S", prompt):
            score += 1.5  # Has markdown headers (raised from 1.0)
        if re.search(r"(?m)^\s*[-*]\s+\S", prompt):
            score += 0.5  # Has bullet points
```

- [ ] **Step 3: Increase specificity per-constraint bonus**

In `heuristic_specificity()` (line 171), raise the per-hit bonus from 1.3 to 1.5:

```python
        score = 2.0 + hits * 1.5  # Raised from 1.3 — 6 hits → 11.0 → capped at 10.0
```

- [ ] **Step 4: Reframe conciseness as information density**

Replace `heuristic_conciseness()` (lines 98-142) with task-relative scoring:

```python
    @staticmethod
    def heuristic_conciseness(prompt: str) -> float:
        """Score prompt conciseness as information density.

        Instead of penalizing length, measures how much of the content
        contributes useful information. A long, structured prompt with
        high information density scores well.

        Scoring:
        - Base: 6.0 (neutral — assume reasonable density)
        - TTR adjustment: higher unique-word ratio → bonus
        - Structural density bonus: headers and lists compress information
        - Filler penalty: detected filler phrases reduce score
        """
        fillers = [
            r"\bplease note that\b",
            r"\bit is (?:very |quite |extremely )?important (?:that|to)\b",
            r"\bmake sure to\b",
            r"\bbasically\b",
            r"\bessentially\b",
            r"\bsort of\b",
            r"\bkind of\b",
            r"\bjust\b",
            r"\bperhaps\b",
            r"\bgenerally\b",
            r"\bas much as possible\b",
            r"\bin a way that\b",
            r"\btry to\b",
        ]

        words = re.findall(r"\b[a-zA-Z']+\b", prompt.lower())
        total = len(words)
        if total == 0:
            return 6.0

        unique = len(set(words))
        ttr = unique / total

        # Base 6.0 + TTR adjustment (0.5 midpoint for long prompts)
        score = 6.0 + (ttr - 0.5) * 4.0

        # Structural density bonus: headers and lists compress information
        headers = len(re.findall(r"(?m)^#{1,6}\s+\S", prompt))
        lists = len(re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt))
        if headers >= 2 and lists >= 2:
            score += 1.0  # Well-organized = dense

        # Filler penalty
        for pattern in fillers:
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            score -= 0.8 * len(matches)

        return round(max(1.0, min(10.0, score)), 2)
```

- [ ] **Step 5: Run tests**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v -k "heuristic or scorer" 2>&1 | tail -20`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/heuristic_scorer.py
git commit -m "feat: recalibrate heuristic scorer — raised ceilings, density-based conciseness"
```

---

### Task 4: Add improvement_score Column

**Files:**
- Modify: `backend/app/models.py`

- [ ] **Step 1: Add column to Optimization model**

After `heuristic_flags` (line 76 of models.py), add:

```python
    improvement_score = Column(Float, nullable=True)  # Weighted delta score (0-10)
```

- [ ] **Step 2: Create the column in SQLite**

Since this project uses aiosqlite without Alembic migrations, add the column directly:

```bash
cd backend && source .venv/bin/activate && python -c "
import sqlite3
conn = sqlite3.connect('../data/synthesis.db')
try:
    conn.execute('ALTER TABLE optimizations ADD COLUMN improvement_score REAL')
    conn.commit()
    print('Column added')
except sqlite3.OperationalError as e:
    if 'duplicate column' in str(e):
        print('Column already exists')
    else:
        raise
conn.close()
"
```

- [ ] **Step 3: Backfill from existing score_deltas**

```bash
cd backend && source .venv/bin/activate && python -c "
import sqlite3, json
conn = sqlite3.connect('../data/synthesis.db')
cursor = conn.execute('SELECT id, score_deltas FROM optimizations WHERE score_deltas IS NOT NULL AND improvement_score IS NULL')
updated = 0
for row in cursor.fetchall():
    opt_id, deltas_json = row
    deltas = json.loads(deltas_json)
    imp = (
        deltas.get('clarity', 0) * 0.25 +
        deltas.get('specificity', 0) * 0.25 +
        deltas.get('structure', 0) * 0.20 +
        deltas.get('faithfulness', 0) * 0.20 +
        deltas.get('conciseness', 0) * 0.10
    )
    imp = round(max(0.0, min(10.0, imp)), 2)
    conn.execute('UPDATE optimizations SET improvement_score = ? WHERE id = ?', (imp, opt_id))
    updated += 1
conn.commit()
print(f'Backfilled {updated} rows')
conn.close()
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models.py
git commit -m "feat: add improvement_score column with backfill"
```

---

### Task 5: Compute improvement_score in Pipeline

**Files:**
- Modify: `backend/app/services/pipeline.py`

- [ ] **Step 1: Find where score_deltas is computed and add improvement_score**

In `pipeline.py`, after `score_deltas` is computed (search for `score_deltas`), add improvement_score computation. The score_deltas dict has keys: clarity, specificity, structure, faithfulness, conciseness.

After the line that sets `score_deltas` on the optimization object, add:

```python
            # Compute weighted improvement score from deltas
            if score_deltas:
                improvement = (
                    score_deltas.get("clarity", 0) * 0.25
                    + score_deltas.get("specificity", 0) * 0.25
                    + score_deltas.get("structure", 0) * 0.20
                    + score_deltas.get("faithfulness", 0) * 0.20
                    + score_deltas.get("conciseness", 0) * 0.10
                )
                db_opt.improvement_score = round(max(0.0, min(10.0, improvement)), 2)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/pipeline.py
git commit -m "feat: compute improvement_score during pipeline scoring phase"
```

---

### Task 6: Update Centroid Weight Formula

**Files:**
- Modify: `backend/app/services/taxonomy/family_ops.py`

- [ ] **Step 1: Replace linear weight with power-law weight**

In `assign_cluster()`, find the line (around line 356-392):

```python
score_weight = max(0.1, (overall_score or 5.0) / 10.0)
```

Replace with:

```python
# Power-law weight for better differentiation (4.25x range vs 1.37x).
# score 3.0 → 0.20, score 5.0 → 0.35, score 7.0 → 0.59, score 9.0 → 0.85
score_weight = max(0.2, ((overall_score or 5.0) / 10.0) ** 1.5)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/taxonomy/family_ops.py
git commit -m "feat: power-law centroid weight for better score differentiation"
```

---

### Task 7: Verify End-to-End

- [ ] **Step 1: Run all backend tests**

Run: `cd backend && source .venv/bin/activate && python -m pytest tests/ -v 2>&1 | tail -20`

- [ ] **Step 2: Restart services and run an optimization**

```bash
./init.sh restart
```

Then optimize a prompt via the MCP tool or UI and check:
- Score distribution: should show wider spread (not all 6.5-7.5)
- Conciseness: structured prompts should score 7+ (not 4-5)
- Faithfulness: intent-serving additions should score 8+ (not 5-6)
- improvement_score: should be populated on new optimizations

- [ ] **Step 3: Check score distribution**

```bash
curl -s http://localhost:8000/api/history?limit=5 | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
for item in d.get('items', []):
    print(f'{item[\"overall_score\"]:5.2f}  imp={item.get(\"improvement_score\", \"?\")}  {item[\"intent_label\"][:40]}')
"
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: scoring decompression — complete implementation"
```
