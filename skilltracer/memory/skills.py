"""Skill storage, identifier allocation and lexical retrieval."""

import json
import math
import os
import re
from typing import Any, Dict, List


class SkillOutputParseError(Exception):
    """Raised when a '''skill ... ''' block cannot be parsed into valid skill JSON."""

    pass


class SkillRepository:
    """Persist reusable skill plans and retrieve candidates for a subgoal."""

    DELIM = "\n--- SKILL ---\n"

    def __init__(self, repo_path: str):
        self.repo_path = repo_path

    # =========================
    # 1) Read all content
    # =========================
    def read_all(self) -> List[Dict[str, Any]]:
        """
        Read all skills from the configured repository,
        parse each '''skill ... ''' block, and return a list of skill objects (dict).

        Malformed skill blocks raise SkillOutputParseError.
        """
        repo_path1 = self.repo_path
        if not os.path.exists(repo_path1):
            return []

        with open(repo_path1, "r", encoding="utf-8") as f:
            content = f.read()

        skills: List[Dict[str, Any]] = []
        opening = re.compile(r"(?mi)^[ \t]*(?P<fence>'''|```)skill(?:[ \t]*\r?\n|[ \t]*(?=\{))")
        cursor = 0
        while content[cursor:].strip():
            match = opening.search(content, cursor)
            if not match or content[cursor : match.start()].strip() not in {"", "--- SKILL ---"}:
                raise SkillOutputParseError("Unframed content in skill repository.")
            body = content[match.end() :]
            offset = len(body) - len(body.lstrip())
            try:
                _, end = json.JSONDecoder().raw_decode(body.lstrip())
            except ValueError as exc:
                raise SkillOutputParseError(f"Invalid JSON in skill repository: {exc}") from exc
            json_end = match.end() + offset + end
            remainder = content[json_end:]
            closing = json_end + len(remainder) - len(remainder.lstrip())
            if not content.startswith(match.group("fence"), closing):
                raise SkillOutputParseError("Skill block is missing its closing fence.")
            skills.append(
                self.parse_skill_json_from_triple_skill(content[match.start() : json_end])
            )
            cursor = closing + 3
        return skills

    @staticmethod
    def parse_skill_json_from_triple_skill(block: str) -> Dict[str, Any]:
        """
        Parse a skill JSON object from a raw '''skill ... ''' block.

        Expected input example:
        '''skill
        {
        "id": "...",
        "name": "...",
        "description": "...",
        "plan": {...}
        }
        '''
        """
        if not isinstance(block, str) or not block.strip():
            raise SkillOutputParseError("Empty or non-string skill block.")

        start = block.find("{")
        if start < 0:
            raise SkillOutputParseError("No JSON object found in skill block.")
        try:
            skill_obj, _ = json.JSONDecoder().raw_decode(block[start:])
        except ValueError as e:
            raise SkillOutputParseError(f"Invalid JSON in skill block: {e}")

        # Validate the required skill fields.
        if not isinstance(skill_obj, dict):
            raise SkillOutputParseError("Skill JSON is not a dict.")

        for k in ("id", "name", "description", "plan"):
            if k not in skill_obj:
                raise SkillOutputParseError(f"Missing required field: {k}")

        if not isinstance(skill_obj.get("plan"), dict):
            raise SkillOutputParseError("Field 'plan' must be a dict.")

        return skill_obj

    @staticmethod
    def upsert_skill(
        skills: List[Dict[str, Any]],
        skill_obj: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Update or insert a skill into the skills repository (in-memory).

        Logic:
        - If a skill with the same id exists, replace it.
        - If not, append the new skill with a normalized auto-generated id:
            s{N+1}, where N is the max existing skill index.
        - If skills is empty, assign id = s1.

        Args:
            skills: List of existing skill objects.
            skill_obj: Newly produced skill object (may contain arbitrary 'id').

        Returns:
            The updated skills list (same list object, mutated in-place).
        """

        # Replace a skill when its identifier already exists.
        incoming_id = skill_obj.get("id", None)
        if incoming_id is not None:
            for idx, existing in enumerate(skills):
                if existing.get("id") == incoming_id:
                    skills[idx] = skill_obj
                    return skills

        # Allocate new identifiers centrally in the repository.
        # Find the largest numeric skill identifier.
        max_idx = 0
        pattern = re.compile(r"^s(\d+)$")

        for s in skills:
            sid = s.get("id")
            if isinstance(sid, str):
                m = pattern.match(sid)
                if m:
                    num = int(m.group(1))
                    if num > max_idx:
                        max_idx = num

        new_id = f"s{max_idx + 1}"

        # Assign the repository-generated identifier.
        skill_obj = dict(skill_obj)  # Avoid mutating the caller-owned object.
        skill_obj["id"] = new_id

        skills.append(skill_obj)
        return skills

    def write_back(self, skills: List[Dict[str, Any]]) -> None:
        """
        Write all skills back to skill_repository.txt (same directory as skill.py).

        Format for each skill:
        '''skill
        { ...pretty json... }
        '''

        Uses atomic write: write to temp file then replace.
        """
        repo_path1 = self.repo_path
        repo_dir = os.path.dirname(repo_path1)
        os.makedirs(repo_dir, exist_ok=True)

        chunks: List[str] = []
        for skill_obj in skills:
            # Keep it simple: require id for sanity
            if not isinstance(skill_obj, dict) or "id" not in skill_obj:
                raise ValueError("Each skill_obj must be a dict containing an 'id' field.")

            json_text = json.dumps(skill_obj, ensure_ascii=False, indent=2, sort_keys=False)
            chunks.append("'''skill\n" + json_text + "\n'''")

        content = "\n\n".join(chunks) + ("\n" if chunks else "")

        tmp_path = repo_path1 + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Atomic replace on most OSes
        os.replace(tmp_path, repo_path1)

    def get_skill_by_id(self, skills: List[Dict[str, Any]], skill_id: str):
        """
        Retrieve a skill object by exact skill id from an in-memory skill list.

        Args:
            skills: list of skill dicts, each must contain key "id"
            skill_id: target skill id to retrieve

        Returns:
            (found: bool, skill: dict or None)
        """
        for skill in skills:
            if skill.get("id") == skill_id:
                return True, skill

        return False, None

    @staticmethod
    def match_by_subgoal(
        skills: List[Dict[str, Any]],
        subgoal: str,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top-k skills by comparing subgoal to each skill["description"]
        using lightweight TF-IDF cosine similarity.

        Args:
            skills: list of skill objects (dict), each should contain "description"
            subgoal: current subgoal string
            top_k: number of top skills to return

        Returns:
            A list of top-k matching skill objects (sorted by similarity desc).
            Returns empty list if no valid match.
        """
        if not skills:
            return []
        if not subgoal or not subgoal.strip():
            return []

        # Build corpus
        docs = []
        valid_skills = []
        for s in skills:
            desc = s.get("description", "")
            if isinstance(desc, str) and desc.strip():
                docs.append(desc.strip())
                valid_skills.append(s)

        if not valid_skills:
            return []

        query = subgoal.strip()

        # Tokenize
        tokenized_docs = [_tokenize_mixed(d) for d in docs]
        tokenized_query = _tokenize_mixed(query)

        if not tokenized_query:
            return []

        # IDF
        idf = _compute_idf(tokenized_docs)

        # Query vector
        q_vec = _tfidf_vec(tokenized_query, idf)

        # Compute scores
        scored = []
        for i, toks in enumerate(tokenized_docs):
            d_vec = _tfidf_vec(toks, idf)
            score = _cosine_sparse(q_vec, d_vec)
            scored.append((score, valid_skills[i]))

        # Sort by similarity desc
        scored.sort(key=lambda x: x[0], reverse=True)

        # Return the highest-scoring candidates.
        top_skills = [skill for score, skill in scored[:top_k] if score > 0.0]

        return top_skills

    # -------------------------
    # Helpers (simple + robust)
    # -------------------------


_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _is_cjk(ch: str) -> bool:
    # Rough CJK Unified Ideographs range
    o = ord(ch)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x20000 <= o <= 0x2A6DF


def _tokenize_mixed(text: str) -> List[str]:
    """
    Mixed tokenizer:
    - English-ish: word tokens
    - Chinese-ish: characters + bigrams (helps semantics a bit without jieba)
    """
    text = text.strip()
    if not text:
        return []

    # English tokens
    words = _WORD_RE.findall(text.lower())

    # CJK chars
    cjk_chars = [ch for ch in text if _is_cjk(ch)]
    cjk_chars = [ch for ch in cjk_chars if ch.strip()]

    # CJK bigrams
    cjk_bigrams = []
    for i in range(len(cjk_chars) - 1):
        cjk_bigrams.append(cjk_chars[i] + cjk_chars[i + 1])

    # Return combined tokens
    return words + cjk_chars + cjk_bigrams


def _compute_idf(tokenized_docs: List[List[str]]) -> Dict[str, float]:
    """
    IDF(t) = log((N + 1) / (df + 1)) + 1
    """
    N = len(tokenized_docs)
    df = {}
    for toks in tokenized_docs:
        seen = set(toks)
        for t in seen:
            df[t] = df.get(t, 0) + 1

    idf = {}
    for t, dft in df.items():
        idf[t] = math.log((N + 1) / (dft + 1)) + 1.0
    return idf


def _tfidf_vec(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    """
    Sparse TF-IDF vector with sublinear TF:
    tf = 1 + log(count)
    tfidf = tf * idf
    """
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1

    vec = {}
    for t, c in tf.items():
        if t in idf:
            vec[t] = (1.0 + math.log(c)) * idf[t]
    return vec


def _cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    """
    Cosine similarity for sparse dict vectors.
    """
    if not a or not b:
        return 0.0

    # dot
    if len(a) > len(b):
        a, b = b, a
    dot = 0.0
    for k, va in a.items():
        vb = b.get(k)
        if vb is not None:
            dot += va * vb

    # norms
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
