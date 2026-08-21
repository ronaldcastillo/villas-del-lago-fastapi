# tiny in-memory stand-in for the bits of the firestore client we use
from datetime import datetime, timezone


class FakeSnap:
    def __init__(self, col, doc_id, data, create_time=None):
        self.id = doc_id
        self._data = data
        self.reference = FakeRef(col, doc_id)
        self.exists = data is not None
        self.create_time = create_time

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeRef:
    def __init__(self, col, doc_id):
        self.col = col
        self.id = doc_id

    def get(self):
        return self.col.snap(self.id)

    def set(self, data, merge=False):
        if merge and self.id in self.col.docs:
            self.col.docs[self.id].update(data)
        else:
            self.col.docs[self.id] = dict(data)

    def update(self, data):
        self.col.docs[self.id].update(data)

    def delete(self):
        self.col.docs.pop(self.id, None)


def _match(data, f):
    v = data.get(f.field_path)
    op, want = f.op_string, f.value
    if op == "==":
        return v == want
    if op == "in":
        return v in want
    if op == "<=":
        return v is not None and v <= want
    if op == "<":
        return v is not None and v < want
    if op == ">=":
        return v is not None and v >= want
    if op == ">":
        return v is not None and v > want
    raise NotImplementedError(op)


class FakeQuery:
    def __init__(self, col, filters=(), order=None, lim=None):
        self.col = col
        self.filters = filters
        self.order = order
        self.lim = lim

    def where(self, filter):
        return FakeQuery(self.col, self.filters + (filter,), self.order, self.lim)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self.col, self.filters, (field, direction), self.lim)

    def limit(self, n):
        return FakeQuery(self.col, self.filters, self.order, n)

    def get(self):
        rows = [(i, d) for i, d in self.col.docs.items() if all(_match(d, f) for f in self.filters)]
        if self.order:
            field, direction = self.order
            rows = [r for r in rows if r[1].get(field) is not None]
            rows.sort(key=lambda r: r[1].get(field), reverse=(direction == "DESCENDING"))
        if self.lim is not None:
            rows = rows[: self.lim]
        return [self.col.snap(i) for i, _ in rows]

    stream = get


class FakeCollection(FakeQuery):
    def __init__(self, name):
        super().__init__(self)
        self.name = name
        self.docs = {}
        self._n = 0

    def snap(self, doc_id):
        data = self.docs.get(doc_id)
        return FakeSnap(self, doc_id, data, create_time=datetime.now(timezone.utc))

    def add(self, data):
        self._n += 1
        doc_id = f"{self.name}{self._n}"
        self.docs[doc_id] = dict(data)
        return None, FakeRef(self, doc_id)

    def document(self, doc_id):
        return FakeRef(self, doc_id)


class FakeBatch:
    def __init__(self):
        self.ops = []

    def update(self, ref, data):
        self.ops.append(("update", ref, data))

    def delete(self, ref):
        self.ops.append(("delete", ref, None))

    def commit(self):
        for op, ref, data in self.ops:
            ref.update(data) if op == "update" else ref.delete()
        self.ops = []


class FakeDB:
    def __init__(self):
        self.cols = {}

    def collection(self, name):
        return self.cols.setdefault(name, FakeCollection(name))

    def document(self, path):
        col, doc_id = path.split("/", 1)
        return self.collection(col).document(doc_id)

    def batch(self):
        return FakeBatch()

    # test helper
    def seed(self, col, doc_id, data):
        self.collection(col).docs[doc_id] = dict(data)
        return self.collection(col).document(doc_id)
