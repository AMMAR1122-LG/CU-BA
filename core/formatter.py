"""
core/formatter.py
------------------
Pipeline Stage 5 — Output Formatter.

Parses the raw Markdown strings produced by the Groq analysis engine into
structured Python dictionaries that the Flask route layer serialises to JSON
and the frontend renders as interactive data cards.

Design rationale:
- The LLM outputs well-structured Markdown with explicit section headings
  defined in our Jinja2 prompt templates.  We exploit that predictable
  structure to split content into named sections without fragile regex.
- Each analysis dimension (explanation, bug detection, improvements) is
  parsed into its own typed dict so the frontend can render each card
  independently and lazily.
- All parsing is defensive: if an expected section heading is absent the
  formatter falls back to returning the full raw text rather than raising.

The single public function `format_output()` accepts an `AnalysisBundle`
and returns a `FormattedOutput` dataclass whose `.to_dict()` method
produces the exact JSON shape the frontend expects.
"""

import re
import logging
from dataclasses import dataclass, field, asdict

from core.engine import AnalysisBundle

logger = logging.getLogger(__name__)

# ---- Section heading pattern ------------------------------------------------
# Matches lines like:  ### 🐛 Bug Report Summary
_HEADING_RE = re.compile(r"^#{1,4}\s+(.+)$", re.MULTILINE)


def _split_into_sections(markdown_text: str) -> dict[str, str]:
    """
    Split a Markdown document into a dict keyed by heading text.

    The value for each key is the body content that follows that heading,
    trimmed of leading/trailing whitespace, up to (but not including)
    the next heading.

    Parameters
    ----------
    markdown_text : str
        Raw Markdown string from the LLM.

    Returns
    -------
    dict[str, str]
        Mapping from heading text (emoji stripped and lowercased for matching)
        to its body content.
    """
    sections: dict[str, str] = {}
    # Match headings with any number of # and any content (including emoji)
    # Use a more permissive pattern
    heading_pattern = re.compile(r"^#+\s+(.+?)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(markdown_text))

    for i, match in enumerate(matches):
        heading_text = match.group(1).strip()
        # Clean key: strip emoji, extra spaces, normalize
        # Emoji ranges: U+1F300-U+1F9FF, U+2600-U+27BF, etc.
        clean_key = re.sub(
            r"[\U0001F000-\U0001FFFF\u2600-\u27BF\u2300-\u23FF\u2000-\u206F\u2700-\u27BF\s]+",
            " ",
            heading_text
        ).strip().lower()
        
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        body = markdown_text[body_start:body_end].strip()
        
        if clean_key:  # Only add non-empty keys
            sections[clean_key] = body

    return sections


def _strip_outer_code_fence(text: str) -> str:
    """
    Remove a single surrounding triple-backtick fence if present.

    Useful when an LLM wraps its entire response in a code block.
    """
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


# ---- Per-analysis parsers ---------------------------------------------------

def _parse_explanation(raw: str) -> dict:
    """
    Parse the explanation LLM response into a structured dict.

    Returns
    -------
    dict with keys:
        overview, breakdown, patterns, gotchas, full_markdown
    """
    sections = _split_into_sections(raw)
    
    # Try multiple heading variations (emoji placement varies)
    overview = ""
    breakdown = ""
    patterns = ""
    gotchas = ""
    
    for key, value in sections.items():
        key_lower = key.lower().strip()
        
        if "overview" in key_lower:
            overview = value
        elif "breakdown" in key_lower or "section" in key_lower:
            breakdown = value
        elif "pattern" in key_lower or "language" in key_lower:
            patterns = value
        elif "gotcha" in key_lower or "complexity" in key_lower or "edge case" in key_lower:
            gotchas = value
    
    # Fallback: if sections are empty, try to extract from raw markdown
    if not overview:
        match = re.search(r"### 🔍.*?Overview\n(.*?)(?=###|$)", raw, re.DOTALL)
        if match:
            overview = match.group(1).strip()
        else:
            # Last resort: first 2-3 paragraphs before first heading
            lines = raw.split('\n')
            para = []
            for line in lines:
                if line.startswith('#'):
                    break
                para.append(line)
            overview = '\n'.join(para).strip() if para else ""
    
    if not breakdown:
        match = re.search(r"###.*?[Ss]ection[- ]by[- ][Ss]ection.*?\n(.*?)(?=###|$)", raw, re.DOTALL)
        if match:
            breakdown = match.group(1).strip()
    
    if not patterns:
        match = re.search(r"###.*?[Ll]anguage.*?\n(.*?)(?=###|$)", raw, re.DOTALL)
        if match:
            patterns = match.group(1).strip()
    
    if not gotchas:
        match = re.search(r"###.*?[Cc]omplexity.*?\n(.*?)(?=###|$)", raw, re.DOTALL)
        if match:
            gotchas = match.group(1).strip()
    
    return {
        "overview": overview or raw[:500] if raw else "",  # Fallback to first part of raw
        "breakdown": breakdown,
        "patterns": patterns,
        "gotchas": gotchas,
        "full_markdown": raw,
    }


def _parse_bug_detection(raw: str) -> dict:
    """
    Parse the bug detection LLM response into a structured dict.

    Extracts individual bug blocks by scanning for **Bug #N** markers
    so the frontend can render each bug as a collapsible card.

    Returns
    -------
    dict with keys:
        summary, bugs (list of dicts), verified_correct, full_markdown
    """
    sections = _split_into_sections(raw)
    
    # Find summary, detailed analysis, and verified sections with flexible matching
    summary = ""
    detailed_block = raw
    verified = ""
    
    for key, value in sections.items():
        if "summary" in key or "bug report" in key:
            summary = value
        elif "detailed" in key or "analysis" in key:
            detailed_block = value
        elif "verified" in key or "correct" in key:
            verified = value
    
    if not summary:
        # Fallback: extract first paragraph or summary-like section
        lines = raw.split('\n')
        para = []
        for line in lines:
            if line.startswith('#') or line.startswith('**'):
                break
            if line.strip():
                para.append(line)
        summary = '\n'.join(para[:3]).strip() if para else "No summary available"
    
    # Extract individual bug entries.
    bugs: list[dict] = []
    # Split on **Bug #N** pattern (flexible for different separators)
    bug_blocks = re.split(r"\*\*Bug\s*#?\d*\s*[—–-]?\s*", detailed_block)

    for block in bug_blocks:
        block = block.strip()
        if not block or len(block) < 10:  # Skip too-small blocks
            continue
        
        lines = block.splitlines()
        title = lines[0].rstrip("*").strip() if lines else "Unknown Bug"
        body = "\n".join(lines[1:]).strip()

        # Extract fields from the body — more flexible regex patterns
        def _extract_field(labels: list, text: str) -> str:
            """Try multiple label variations."""
            for label in labels:
                pattern = rf"\*\*{label}:\*\*\s*(.+?)(?=\n\*\*|\Z)"
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    return match.group(1).strip()
            return ""

        severity = _extract_field(["Severity", "severity"], body)
        bug_type = _extract_field(["Type", "type", "Bug Type"], body)
        location = _extract_field(["Location", "location", "Line", "File"], body)
        root_cause = _extract_field(["Root Cause", "root cause", "Cause"], body)

        # Extract the fix code block.
        fix_match = re.search(r"```(?:\w+)?\n(.*?)```", body, re.DOTALL)
        fix_code = fix_match.group(1).strip() if fix_match else ""

        bugs.append({
            "title": title,
            "type": bug_type,
            "location": location,
            "root_cause": root_cause,
            "severity": severity or "Medium",
            "fix_code": fix_code,
            "raw_block": block,
        })

    return {
        "summary": summary,
        "bugs": bugs,
        "bug_count": len(bugs),
        "verified_correct": verified or "_All analyzed code paths are correct._",
        "full_markdown": raw,
    }


def _parse_improvement(raw: str) -> dict:
    """
    Parse the improvement recommendations LLM response into a structured dict.

    Returns
    -------
    dict with keys:
        scores, recommendations (list of dicts), strengths, full_markdown
    """
    sections = _split_into_sections(raw)
    
    scores_block = ""
    strengths = ""
    recs_block = raw
    
    for key, value in sections.items():
        if "score" in key or "quality" in key:
            scores_block = value
        elif "strength" in key or "well" in key or "positive" in key:
            strengths = value
        elif "recommend" in key or "improvement" in key:
            recs_block = value

    # Parse score lines: "- **Readability:** 7/10" or "Readability: 7/10"
    scores: dict[str, str] = {}
    
    # Try multiple patterns
    score_patterns = [
        r"\*\*(.+?):\*\*\s*(\d+/10)",  # **Label:** 7/10
        r"(?:^|\n)[-*]?\s*\*\*(.+?):\*\*\s*(\d+/10)",  # - **Label:** 7/10
        r"(?:^|\n)[-*]?\s*(.+?):\s*(\d+/10)",  # - Label: 7/10
    ]
    
    for pattern in score_patterns:
        matches = re.finditer(pattern, scores_block)
        for match in matches:
            label = match.group(1).strip()
            score = match.group(2).strip()
            if label and score:
                scores[label] = score

    # Extract individual improvement entries.
    improvements: list[dict] = []
    imp_blocks = re.split(r"\*\*Improvement\s*#?\d*\s*[—–-]?\s*", recs_block)

    for block in imp_blocks:
        block = block.strip()
        if not block or len(block) < 10:
            continue
        
        lines = block.splitlines()
        title = lines[0].rstrip("*").strip() if lines else "Improvement"
        body = "\n".join(lines[1:]).strip()

        def _extract_field(labels: list, text: str) -> str:
            """Try multiple label variations."""
            for label in labels:
                pattern = rf"\*\*{label}:\*\*\s*(.+?)(?=\n\*\*|\Z)"
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    return match.group(1).strip()
            return ""

        category = _extract_field(["Category", "category"], body)
        priority = _extract_field(["Priority", "priority"], body) or "Medium"
        issue = _extract_field(["Current Issue", "current issue", "Issue"], body)
        rationale = _extract_field(["Rationale", "rationale", "Why"], body)

        code_match = re.search(r"```(?:\w+)?\n(.*?)```", body, re.DOTALL)
        code_snippet = code_match.group(1).strip() if code_match else ""

        improvements.append({
            "title": title,
            "category": category,
            "priority": priority,
            "issue": issue,
            "rationale": rationale,
            "code_snippet": code_snippet,
            "raw_block": block,
        })

    return {
        "scores": scores,
        "recommendations": improvements,
        "recommendation_count": len(improvements),
        "strengths": strengths or "_The code demonstrates good practices._",
        "full_markdown": raw,
    }


# ---- Public API ------------------------------------------------------------

@dataclass
class FormattedOutput:
    """
    Fully structured analysis result ready for JSON serialisation.

    Attributes
    ----------
    classification : str
        The input type label from the classifier stage.
    explanation : dict
        Parsed explanation data.
    bug_detection : dict
        Parsed bug report data including individual bug entries.
    improvement : dict
        Parsed improvement recommendations with quality scores.
    """

    classification: str
    explanation: dict = field(default_factory=dict)
    bug_detection: dict = field(default_factory=dict)
    improvement: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise the result to a plain nested dictionary for JSON output."""
        return asdict(self)


def format_output(bundle: AnalysisBundle) -> FormattedOutput:
    """
    Convert a raw `AnalysisBundle` from the engine into structured UI data.

    Parameters
    ----------
    bundle : AnalysisBundle
        Raw LLM outputs and classification label from the engine stage.

    Returns
    -------
    FormattedOutput
        Structured, JSON-serialisable result object.
    """
    logger.debug("Formatting output for classification='%s'.", bundle.classification_label)

    explanation = _parse_explanation(bundle.explanation_raw)
    bug_detection = _parse_bug_detection(bundle.bug_detection_raw)
    improvement = _parse_improvement(bundle.improvement_raw)

    return FormattedOutput(
        classification=bundle.classification_label,
        explanation=explanation,
        bug_detection=bug_detection,
        improvement=improvement,
    )