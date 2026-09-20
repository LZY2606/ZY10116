"""Tests for opt-in dynamic-default dependency declarations.

A default generator decorated with ``@depends_on(...)`` declares which other
traits it reads. As long as its value has not been assigned explicitly, a
change of any declared dependency invalidates the cached default, which is
recomputed lazily on the next access.
"""

from __future__ import annotations

import pickle

import pytest

from traitlets import (
    All,
    Float,
    HasTraits,
    Int,
    TraitError,
    Unicode,
    default,
    depends_on,
    validate,
)
from traitlets.config import Config, Configurable


class Window(HasTraits):
    """frequency/duration -> sampled window size."""

    frequency = Float(10.0)
    duration = Float(1.0)
    window = Int()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.window_calls = 0

    @default("window")
    @depends_on("frequency", "duration")
    def _window_default(self):
        self.window_calls += 1
        return int(self.frequency * self.duration)


def test_default_computed_once_on_first_access():
    w = Window()
    assert w.window_calls == 0
    assert w.window == 10
    assert w.window == 10
    assert w.window_calls == 1


def test_dependency_change_invalidates_and_recomputes():
    w = Window()
    assert w.window == 10
    w.frequency = 20.0
    assert not w.trait_has_value("window")
    assert w.window == 20
    w.duration = 2.0
    assert w.window == 40
    assert w.window_calls == 3


def test_no_event_on_invalidation_change_event_on_recompute():
    w = Window()
    assert w.window == 10
    events = []
    w.observe(
        lambda c: events.append((c.name, c.type, c.get("old"), c.get("new", c.get("value")))),
        names=All,
        type=All,
    )
    w.frequency = 20.0
    # Invalidation itself emits no event.
    assert events == [("frequency", "change", 10.0, 20.0)]
    assert w.window == 20
    # Recomputation follows the usual lazy "default" event with no old value.
    assert ("window", "default", None, 20) in events


def test_explicit_assignment_is_protected():
    w = Window()
    assert w.window == 10
    w.window = 99
    assert not w.trait_is_default("window")
    w.frequency = 30.0
    w.duration = 5.0
    assert w.window == 99
    assert w.window_calls == 1


def test_explicit_assignment_equal_to_derived_is_still_explicit():
    w = Window()
    assert w.window == 10
    w.window = 10
    w.frequency = 50.0
    assert w.window == 10
    assert w.window_calls == 1


def test_deleting_explicit_value_restores_derived_mode():
    w = Window()
    assert w.window == 10
    w.window = 99
    w.frequency = 30.0
    assert w.window == 99
    del w.window
    assert not w.trait_has_value("window")
    assert w.window == 30
    assert w.window_calls == 2
    assert w.trait_is_default("window")


def test_deleting_derived_value_also_restores_lazy_default():
    w = Window()
    assert w.window == 10
    del w.window
    assert w.window == 10
    assert w.window_calls == 2


def test_init_kwargs_make_value_explicit():
    w = Window(window=7)
    w.frequency = 100.0
    assert w.window == 7
    assert w.window_calls == 0
    assert not w.trait_is_default("window")
    del w.window
    assert w.window == 100
    assert w.window_calls == 1


def test_undecorated_default_is_unchanged():
    class Plain(HasTraits):
        a = Int(1)
        b = Int()

        calls = 0

        @default("b")
        def _b_default(self):
            self.calls += 1
            return self.a * 2

    p = Plain()
    assert p.b == 2
    p.a = 5
    # historical behavior: cached default is kept
    assert p.b == 2
    assert p.calls == 1


def test_decorator_requires_string_names():
    with pytest.raises(TypeError):
        depends_on()
    with pytest.raises(TypeError):
        depends_on(3)
    with pytest.raises(TypeError):
        depends_on("a", extend=True, replace=True)


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


class BaseDerived(HasTraits):
    a = Int(1)
    b = Int(10)
    c = Int(100)
    x = Int()

    @default("x")
    @depends_on("a")
    def _x_default(self):
        return self.a


class ExtendedDerived(BaseDerived):
    @depends_on("b", extend=True)
    def _x_default(self):  # type: ignore[override]
        return self.a + self.b


class ReplacedDerived(BaseDerived):
    @depends_on("c")
    def _x_default(self):  # type: ignore[override]
        return self.c


def test_inherited_dependencies_are_visible():
    assert BaseDerived.default_dependencies("x") == ("a",)


def test_extend_keeps_inherited_dependencies():
    assert ExtendedDerived.default_dependencies("x") == ("a", "b")
    e = ExtendedDerived()
    assert e.x == 11
    e.a = 2
    assert e.x == 12
    e.b = 20
    assert e.x == 22


def test_replace_drops_inherited_dependencies():
    assert ReplacedDerived.default_dependencies("x") == ("c",)
    r = ReplacedDerived()
    assert r.x == 100
    r.a = 5
    assert r.x == 100
    r.c = 200
    assert r.x == 200


def test_extend_decorator_stacks_above_default():
    class Both(BaseDerived):
        @default("x")
        @depends_on("b", extend=True)
        def _x_default(self):  # type: ignore[override]
            return self.a * self.b

    assert Both.default_dependencies("x") == ("a", "b")
    inst = Both()
    assert inst.x == 10
    inst.b = 3
    assert inst.x == 3


# ---------------------------------------------------------------------------
# Multi-level / diamond propagation and exact call counts
# ---------------------------------------------------------------------------


class Diamond(HasTraits):
    a = Int(1)
    b = Int()
    c = Int()
    d = Int()

    b_calls = c_calls = d_calls = 0

    @default("b")
    @depends_on("a")
    def _b_default(self):
        self.b_calls += 1
        return self.a * 2

    @default("c")
    @depends_on("a")
    def _c_default(self):
        self.c_calls += 1
        return self.a + 1

    @default("d")
    @depends_on("b", "c")
    def _d_default(self):
        self.d_calls += 1
        return self.b + self.c


def test_diamond_computes_each_default_exactly_once():
    d = Diamond()
    assert d.d == 4
    assert (d.b_calls, d.c_calls, d.d_calls) == (1, 1, 1)


def test_diamond_invalidates_each_node_once():
    d = Diamond()
    assert d.d == 4
    d.a = 10
    assert d.d == 31
    # b and c both depend on a, d on both: each is recomputed exactly once.
    assert (d.b_calls, d.c_calls, d.d_calls) == (2, 2, 2)


def test_diamond_explicit_middle_node_blocks_propagation():
    d = Diamond()
    assert d.d == 4
    d.b = 100
    d.a = 10
    # b was set explicitly and stays frozen; c is derived, d derives through b.
    assert d.b == 100
    assert d.c == 11
    assert d.d == 111
    assert (d.b_calls, d.c_calls, d.d_calls) == (1, 2, 2)


def test_chained_invalidation_propagates_topologically():
    class Chain(HasTraits):
        t0 = Int(1)
        t1 = Int()
        t2 = Int()
        t3 = Int()

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        @default("t1")
        @depends_on("t0")
        def _t1_default(self):
            self.calls += 1
            return self.t0 + 1

        @default("t2")
        @depends_on("t1")
        def _t2_default(self):
            self.calls += 1
            return self.t1 + 1

        @default("t3")
        @depends_on("t2")
        def _t3_default(self):
            self.calls += 1
            return self.t2 + 1

    ch = Chain()
    assert ch.t3 == 4
    ch.t0 = 10
    assert ch.t3 == 13
    assert ch.calls == 6


def test_long_chain_does_not_recurse():
    # A chain far longer than sys.getrecursionlimit() must not overflow.
    n = 4000
    body = ["class Long(HasTraits):"]
    body += [f"    t{i} = Int({'1' if i == 0 else '0'})" for i in range(n + 1)]
    for i in range(n):
        body.append(f"    @default('t{i + 1}')")
        body.append(f"    @depends_on('t{i}')")
        body.append(f"    def _d{i}(self):")
        body.append(f"        return self.t{i} + 1")
    ns = {"HasTraits": HasTraits, "Int": Int, "default": default, "depends_on": depends_on}
    exec("\n".join(body), ns)  # noqa: S102
    long = ns["Long"]()
    assert getattr(long, f"t{n}") == n + 1
    long.t0 = 5
    assert getattr(long, f"t{n}") == n + 5


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def test_direct_cycle_raises_with_path():
    with pytest.raises(TraitError, match=r"x -> y -> x"):

        class Cyclic(HasTraits):
            x = Int()
            y = Int()

            @default("x")
            @depends_on("y")
            def _x_default(self):
                return self.y

            @default("y")
            @depends_on("x")
            def _y_default(self):
                return self.x


def test_indirect_cycle_raises_with_full_path():
    with pytest.raises(TraitError, match=r"p -> q -> r -> p"):

        class Indirect(HasTraits):
            p = Int()
            q = Int()
            r = Int()

            @default("p")
            @depends_on("q")
            def _p_default(self):
                return self.q

            @default("q")
            @depends_on("r")
            def _q_default(self):
                return self.r

            @default("r")
            @depends_on("p")
            def _r_default(self):
                return self.p


def test_self_dependency_cycle_raises():
    with pytest.raises(TraitError, match=r"z -> z"):

        class Self(HasTraits):
            z = Int()

            @default("z")
            @depends_on("z")
            def _z_default(self):
                return self.z


# ---------------------------------------------------------------------------
# hold_trait_notifications
# ---------------------------------------------------------------------------


def test_hold_merges_dependency_change_events():
    w = ExtendedDerived()
    assert w.x == 11
    events = []
    w.observe(lambda c: events.append((c.name, c.old, c.new)), names=["a", "b", "x"])
    with w.hold_trait_notifications():
        w.a = 3
        w.b = 30
        # lazy recomputation inside the hold sees the new dependencies
        assert w.x == 33
    # one compressed change event per dependency; x is invalidated, not set
    assert ("a", 1, 3) in events
    assert ("b", 10, 30) in events
    assert not any(name == "x" for name, _, _ in events)
    assert w.x == 33


def test_hold_recompute_default_event_observers():
    w = Window()
    assert w.window == 10
    default_events = []
    w.observe(lambda c: default_events.append(c.value), names="window", type="default")
    with w.hold_trait_notifications():
        w.frequency = 4.0
        assert w.window == 4
    # default events are emitted lazily at recomputation (historical behavior)
    assert default_events == [4]


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


def test_validator_runs_on_explicit_set_not_on_recomputed_default():
    class Validated(HasTraits):
        a = Int(1)
        x = Int()

        validations = 0

        @default("x")
        @depends_on("a")
        def _x_default(self):
            return self.a * 2

        @validate("x")
        def _x_validate(self, proposal):
            self.validations += 1
            if proposal.value < 0:
                raise TraitError("negative")
            return proposal.value

    v = Validated()
    assert v.x == 2
    v.a = 3
    assert v.x == 6
    # lazy defaults are cross-validation locked, like ordinary dynamic defaults
    assert v.validations == 0
    v.x = 5
    assert v.validations == 1
    with pytest.raises(TraitError):
        v.x = -1


# ---------------------------------------------------------------------------
# failure and retry
# ---------------------------------------------------------------------------


def test_failed_default_is_not_cached_and_retries():
    class Flaky(HasTraits):
        ready = Int(0)
        x = Int()

        attempts = 0

        @default("x")
        @depends_on("ready")
        def _x_default(self):
            self.attempts += 1
            if self.ready == 0:
                raise ValueError("not ready")
            return self.ready * 7

    f = Flaky()
    with pytest.raises(ValueError, match="not ready"):
        f.x
    assert not f.trait_has_value("x")
    assert f.attempts == 1
    f.ready = 1
    assert f.x == 7
    assert f.attempts == 2


def test_failed_default_emits_no_default_event():
    class Boom(HasTraits):
        x = Int(0)
        y = Int()

        @default("y")
        @depends_on("x")
        def _y_default(self):
            raise RuntimeError("boom")

    b = Boom()
    events = []
    b.observe(events.append, names=All, type=All)
    with pytest.raises(RuntimeError, match="boom"):
        b.y
    assert not any(event["name"] == "y" for event in events)
    assert not b.trait_has_value("y")


# ---------------------------------------------------------------------------
# Configuration injection
# ---------------------------------------------------------------------------


class ConfiguredDerived(Configurable):
    factor = Int(3).tag(config=True)
    value = Int().tag(config=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    @default("value")
    @depends_on("factor")
    def _value_default(self):
        self.calls += 1
        return self.factor * 2


def test_configured_derived_default_reacts_to_config_dependency():
    c = ConfiguredDerived(config=Config({"ConfiguredDerived": {"factor": 9}}))
    assert c.value == 18
    assert c.calls == 1
    c.factor = 10
    assert c.value == 20


def test_configured_explicit_value_is_protected():
    c = ConfiguredDerived(config=Config({"ConfiguredDerived": {"value": 7}}))
    assert c.value == 7
    assert c.calls == 0
    c.factor = 50
    assert c.value == 7
    del c.value
    assert c.value == 100
    assert c.calls == 1


# ---------------------------------------------------------------------------
# Dynamic traits and unknown dependency names
# ---------------------------------------------------------------------------


def test_dynamically_added_trait_picks_up_declaration():
    class Late(HasTraits):
        a = Int(2)

        @default("z")
        @depends_on("a")
        def _z_default(self):
            return self.a * 4

    late = Late()
    late.add_traits(z=Int())
    assert late.z == 8
    late.a = 3
    assert late.z == 12
    late.z = 100
    late.a = 9
    assert late.z == 100
    del late.z
    assert late.z == 36


def test_unknown_dependency_raises_on_first_use():
    class Bad(HasTraits):
        a = Int(1)
        x = Int()

        @default("x")
        @depends_on("a", "missing")
        def _x_default(self):
            return self.a

    bad = Bad()
    with pytest.raises(TraitError, match=r"'missing'.*not a trait"):
        bad.a = 2


def test_unknown_dependency_does_not_fire_until_exercised():
    # Reading an unrelated derived value must not require every declaration
    # to resolve.
    class Mixed(HasTraits):
        a = Int(1)
        b = Int()

        @default("b")
        @depends_on("a")
        def _b_default(self):
            return self.a

        @default("x")
        @depends_on("nope")
        def _x_default(self):
            return 1

    mixed = Mixed()
    assert mixed.b == 1


# ---------------------------------------------------------------------------
# Instance isolation and pickling
# ---------------------------------------------------------------------------


def test_instances_do_not_share_caches_or_explicit_state():
    one = ExtendedDerived()
    two = ExtendedDerived()
    assert one.x == 11
    assert two.x == 11
    one.a = 5
    assert one.x == 15
    assert two.x == 11
    one.x = 99
    assert two.trait_is_default("x")


def test_explicit_state_survives_pickle():
    w = Window()
    assert w.window == 10
    w.window = 55
    restored = pickle.loads(pickle.dumps(w))
    restored.frequency = 80.0
    assert restored.window == 55
    del restored.window
    assert restored.window == 80


# ---------------------------------------------------------------------------
# Observer old/new values
# ---------------------------------------------------------------------------


def test_observer_sees_correct_old_new_through_lifecycle():
    w = Window()
    assert w.window == 10
    events = []
    w.observe(lambda c: events.append((c.old, c.new)), names="window")
    w.frequency = 3.0  # invalidation only, no window event yet
    assert w.window == 3  # default event is not a "change" event
    w.window = 77
    assert events == [(3, 77)]
    w.window = 88
    assert events == [(3, 77), (77, 88)]


def test_unicode_dependencies_and_observer_events():
    class Named(HasTraits):
        first = Unicode("Ada")
        last = Unicode("Lovelace")
        full = Unicode()

        @default("full")
        @depends_on("first", "last")
        def _full_default(self):
            return f"{self.first} {self.last}"

    n = Named()
    assert n.full == "Ada Lovelace"
    events = []
    n.observe(lambda c: events.append((c.old, c.new)), names="full")
    n.first = "Grace"
    assert n.full == "Grace Lovelace"
    n.full = "fixed"
    assert events == [("Grace Lovelace", "fixed")]


def test_set_trait_marks_explicit_and_delete_restores():
    class S(HasTraits):
        a = Int(1)
        x = Int()

        @default("x")
        @depends_on("a")
        def _x_default(self):
            return self.a * 2

    s = S()
    assert s.x == 2
    s.set_trait("x", 50)
    s.a = 9
    assert s.x == 50
    del s.x
    assert s.x == 18


def test_hold_rollback_restores_derived_cache():
    class R(HasTraits):
        a = Int(1)
        x = Int()

        calls = 0

        @default("x")
        @depends_on("a")
        def _x_default(self):
            self.calls += 1
            return self.a * 2

    r = R()
    assert r.x == 2
    with (  # noqa: PT012
        pytest.raises(TraitError),
        r.hold_trait_notifications(),
    ):
        r.a = 5
        assert r.x == 10
        r.x = "bad"  # fails cross validation at context exit
    assert r.a == 1
    assert r.x == 2


def test_dependencies_declared_via_trait_metadata():
    class Tagged(HasTraits):
        a = Int(3)
        x = Int().tag(depends_on=("a",))

        @default("x")
        def _x_default(self):
            return self.a + 1

    assert Tagged.default_dependencies("x") == ("a",)
    t = Tagged()
    assert t.x == 4
    t.a = 10
    assert t.x == 11


def test_diamond_end_invalidated_once_with_counters():
    d = Diamond()

    class Recorder:
        def __init__(self):
            self.events = []

        def __call__(self, change):
            self.events.append(change)

    rec = Recorder()
    d.observe(rec, names=["b", "c", "d"], type="default")
    assert d.d == 4
    rec.events.clear()
    d.a = 20
    assert d.b == 40
    assert d.c == 21
    assert d.d == 61
    # exactly one default event per node despite the diamond
    assert sorted(event.name for event in rec.events) == ["b", "c", "d"]
    assert (d.b_calls, d.c_calls, d.d_calls) == (2, 2, 2)


def test_read_only_dependent_still_invalidates_until_explicit():
    class RO(HasTraits):
        a = Int(1)
        x = Int(read_only=True)

        @default("x")
        @depends_on("a")
        def _x_default(self):
            return self.a * 5

    r = RO()
    assert r.x == 5
    r.a = 2
    assert r.x == 10
    r.set_trait("x", 77)
    r.a = 3
    assert r.x == 77


def test_legacy_pickle_without_explicit_state_treats_values_as_explicit():
    class P(HasTraits):
        a = Int(1)
        x = Int()

        @default("x")
        @depends_on("a")
        def _x_default(self):
            return self.a

    p = P()
    p.x = 42
    state = p.__getstate__()
    del state["_trait_explicit_values"]
    restored = P.__new__(P)
    restored.__setstate__(state)
    restored.a = 9
    assert restored.x == 42


def test_string_dependency_metadata_supported():
    class Tagged(HasTraits):
        a = Int(1)
        x = Int().tag(depends_on="a")

        @default("x")
        def _x_default(self):
            return self.a + 100

    assert Tagged.default_dependencies("x") == ("a",)
    t = Tagged()
    assert t.x == 101
    t.a = 2
    assert t.x == 102


def test_undeclared_default_read_keeps_historical_lazy_semantics():
    class Mixed(HasTraits):
        a = Int(1)
        b = Int()
        c = Int()

        @default("b")
        def _b_default(self):
            # reads a without a @depends_on declaration
            return self.a + 1

        @default("c")
        @depends_on("b")
        def _c_default(self):
            return self.b + 1

    m = Mixed()
    assert m.c == 3
    m.a = 10
    # b was cached without a declaration: it is not invalidated by a
    assert m.c == 3
    del m.b
    assert m.c == 12
