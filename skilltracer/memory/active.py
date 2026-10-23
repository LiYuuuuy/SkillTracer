"""Keep task-local observations as independently undoable evidence records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryWrite:
    node_id: str
    bid: str
    memory_id: str


class ActiveMemory:
    def __init__(self):
        self._store = {}
        self._record_bids = {}
        self._versions = {}
        self._next_version = {}

    def memorize(self, bid, axtree, description="", *, source_url=None):
        if not bid or str(bid).lower() == "null":
            return None
        if not isinstance(axtree, str) or not axtree.strip():
            return None
        lines = axtree.splitlines()
        for index, line in enumerate(lines):
            if line.lstrip().startswith(f"[{bid}]"):
                indentation = len(line) - len(line.lstrip())
                subtree = [line]
                for child in lines[index + 1 :]:
                    if not child.strip():
                        continue
                    if len(child) - len(child.lstrip()) <= indentation:
                        break
                    subtree.append(child)
                content = "\n".join(subtree).strip()
                if description:
                    content = f"[MEMORY_INTENT]\n{description}\n[AXTREE_SLICE]\n{content}"
                if source_url:
                    content = f"[SOURCE_URL]\n{source_url}\n{content}"
                self._append(str(bid), content)
                return content
        return None

    def _append(self, bid, content):
        version = self._next_version.get(bid, 0) + 1
        memory_id = bid if version == 1 else f"{bid}@{version}"
        while memory_id in self._store:
            version += 1
            memory_id = f"{bid}@{version}"
        self._next_version[bid] = version
        self._store[memory_id] = content
        self._record_bids[memory_id] = bid
        self._versions.setdefault(bid, []).append(memory_id)

    def latest_record_id(self, bid):
        versions = self._versions.get(str(bid), [])
        return versions[-1] if versions else None

    def discard_record(self, memory_id):
        """Undo one produced version without removing older observed evidence."""
        bid = self._record_bids.pop(memory_id, None)
        if bid is None:
            return False
        del self._store[memory_id]
        versions = self._versions[bid]
        if versions[-1] == memory_id:
            versions.pop()
        else:
            versions.remove(memory_id)
        if not versions:
            del self._versions[bid]
        return True

    def delete_memory_by_bid(self, bid):
        versions = list(self._versions.get(str(bid), []))
        for memory_id in reversed(versions):
            self.discard_record(memory_id)
        return bool(versions)

    def get_memory_by_bid(self, bid):
        return self._store.get(self.latest_record_id(bid))

    def restore_memory_by_bid(self, bid, previous):
        """Support older execution batches that stored the previous content only."""
        if self.get_memory_by_bid(bid) == previous:
            return
        memory_id = self.latest_record_id(bid)
        if memory_id is not None:
            self.discard_record(memory_id)
        if previous is not None and self.get_memory_by_bid(bid) != previous:
            self._append(str(bid), previous)

    def to_dict(self):
        return dict(self._store)

    def to_prompt(self):
        return "\n".join(
            f"[MEMORY {memory_id} | OBSERVED BID {self._record_bids[memory_id]}]\n{content}"
            for memory_id, content in self._store.items()
        )
