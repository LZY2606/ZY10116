"""Tests for opt-in default-value dependencies and invalidation."""

# Copyright (c) IPython Development Team.
# Distributed under the terms of the Modified BSD License.

from __future__ import annotations

from collections import Counter

import pytest

from traitlets import Float, HasTraits, Int, TraitError, default
from traitlets.config import Config, Configurable


def counter(obj):
    """Per-instance invocation counter for default generators."""
    try:
        return obj._default_calls
    except AttributeError:
        obj._default_calls = Counter()
        return obj._default_calls


class Sampler(HasTraits):
    """A sampling window derived from a frequency and a duration."""

    freq = Int(100)
    duration = Float(1.0)
    window = Int()

    @default("window", depends_on=["freq", "duration"])
    def _window_default(self):
        counter(self)["window"] += 1
        return int(self.freq * self.duration)


class Chain(HasTraits):
    a = Int(1)
    b = Int()
    c = Int()

    @default("b", depends_on=["a"])
    def _b_default(self):
        counter(self)["b"] += 1
        return self.a + 1

    @default("c", depends_on=["b"])
    def _c_default(self):
        counter(self)["c"] += 1
        return self.b * 10


class Diamond(HasTraits):
    a = Int(1)
    b = Int()
    c = Int()
    d = Int()

    @default("b", depends_on=["a"])
    def _b_default(self):
        counter(self)["b"] += 1
        return self.a + 1

    @default("c", depends_on=["a"])
    def _c_default(self):
        counter(self)["c"] += 1
        return self.a * 2

    @default("d", depends_on=["b", "c"])
    def _d_default(self):
        counter(self)["d"] += 1
        return self.b + self.c


def test_lazy_invalidation_and_recompute():
    s = Sampler()
    assert s.window == 100
    assert counter(s)["window"] == 1
    # changing a dependency does not eagerly recompute the default
    s.freq = 200
    assert counter(s)["window"] == 1
    # the cached value was invalidated, so the next access recomputes
    assert s.window == 200
    assert counter(s)["window"] == 2
    # further reads are cached again
    assert s.window == 200
    assert counter(s)["window"] == 2


def test_multiple_dependencies():
    s = Sampler()
    assert s.window == 100
    s.duration = 2.5
    assert s.window == 250
    assert counter(s)["window"] == 2


def test_explicit_assignment_is_not_overwritten():
    s = Sampler()
    assert s.window == 100
    s.window = 42
    s.freq = 5
    s.duration = 3.0
    assert s.window == 42
    assert counter(s)["window"] == 1


def test_explicit_assignment_before_first_access():
    s = Sampler()
    s.window = 7
    s.freq = 5
    assert s.window == 7
    assert counter(s)["window"] == 0


def test_keyword_argument_is_explicit():
    s = Sampler(window=7)
    s.freq = 999
    assert s.window == 7
    assert counter(s)["window"] == 0


def test_delete_restores_derived_default():
    s = Sampler()
    s.window = 42
    assert s.window == 42
    del s.window
    # back in derived mode: the default is recomputed ...
    assert s.window == 100
    assert counter(s)["window"] == 1
    # ... and dependencies invalidate it again
    s.freq = 200
    assert s.window == 200
    assert counter(s)["window"] == 2


def test_delete_without_access_first():
    s = Sampler()
    s.window = 42
    del s.window
    assert not s.trait_has_value("window")
    assert s.window == 100


class ConfigurableSampler(Configurable):
    freq = Int(100).tag(config=True)
    duration = Float(1.0).tag(config=True)
    window = Int().tag(config=True)

    @default("window", depends_on=["freq", "duration"])
    def _window_default(self):
        counter(self)["window"] += 1
        return int(self.freq * self.duration)


def test_config_value_is_explicit():
    s = ConfigurableSampler(config=Config({"ConfigurableSampler": {"window": 7}}))
    assert s.window == 7
    s.freq = 10
    assert s.window == 7
    assert counter(s)["window"] == 0


def test_configured_dependency_still_invalidates():
    s = ConfigurableSampler(config=Config({"ConfigurableSampler": {"freq": 5}}))
    assert s.window == 5
    assert counter(s)["window"] == 1
    s.freq = 10
    assert s.window == 10
    assert counter(s)["window"] == 2


def test_chain_propagates_topologically():
    ch = Chain()
    assert ch.c == 20
    assert counter(ch)["b"] == 1
    assert counter(ch)["c"] == 1
    ch.a = 2
    # lazy: nothing recomputed until accessed
    assert counter(ch)["b"] == 1
    assert counter(ch)["c"] == 1
    assert ch.c == 30
    assert counter(ch)["b"] == 2
    assert counter(ch)["c"] == 2


def test_chain_stops_at_explicit_value():
    ch = Chain()
    assert ch.c == 20
    ch.b = 5  # explicit: no longer derived from a
    ch.a = 100
    # c still holds a valid cached value derived from b == 5
    assert ch.c == 50
    assert counter(ch)["b"] == 1
    assert counter(ch)["c"] == 2


def test_delete_dependency_invalidates_dependents():
    ch = Chain()
    assert ch.c == 20
    ch.b = 5
    assert ch.c == 50
    del ch.b
    # b is derived again, and c was invalidated with it
    assert ch.c == 20
    assert counter(ch)["b"] == 2
    assert counter(ch)["c"] == 3


def test_diamond_invalidates_end_once():
    d = Diamond()
    assert d.d == 4
    assert counter(d)["b"] == 1
    assert counter(d)["c"] == 1
    assert counter(d)["d"] == 1
    d.a = 10
    assert d.d == 31
    # each default was recomputed exactly once
    assert counter(d)["b"] == 2
    assert counter(d)["c"] == 2
    assert counter(d)["d"] == 2


def test_inheritance_extends_dependencies():
    class Extended(Sampler):
        scale = Int(2)
        scaled_window = Int()

        @default("scaled_window", depends_on=["window", "scale"])
        def _scaled_window_default(self):
            counter(self)["scaled_window"] += 1
            return self.window * self.scale

    e = Extended()
    assert e.scaled_window == 200
    # inherited behaviour still works
    e.freq = 300
    assert e.window == 300
    assert e.scaled_window == 600
    assert counter(e)["scaled_window"] == 2
    e.scale = 3
    assert e.scaled_window == 900
    assert counter(e)["scaled_window"] == 3
    # base class is unaffected by the subclass declarations
    assert "scaled_window" not in Sampler._all_trait_default_dependencies


def test_inheritance_replaces_dependencies():
    class Replaced(HasTraits):
        a = Int(1)
        x = Int(5)
        b = Int()

        @default("b", depends_on=["a"])
        def _b_default(self):
            counter(self)["b"] += 1
            return self.a + self.x

    class Replacing(Replaced):
        @default("b", depends_on=["x"])
        def _b_default2(self):
            counter(self)["b"] += 1
            return self.a + self.x

    r = Replacing()
    assert r.b == 6
    # the replaced declaration no longer tracks "a"
    r.a = 100
    assert r.b == 6
    assert counter(r)["b"] == 1
    # ... but tracks "x"
    r.x = 50
    assert r.b == 150
    assert counter(r)["b"] == 2
    # the parent class keeps its own declaration
    base = Replaced()
    assert base.b == 6
    base.a = 100
    assert base.b == 105


def test_metadata_declaration():
    class Meta(HasTraits):
        a = Int(2)
        b = Int().tag(default_depends_on=["a"])

        @default("b")
        def _b_default(self):
            counter(self)["b"] += 1
            return self.a * 5

    m = Meta()
    assert m.b == 10
    m.a = 3
    assert m.b == 15
    assert counter(m)["b"] == 2


def test_instances_do_not_share_state():
    s1, s2 = Sampler(), Sampler()
    assert s1.window == 100
    assert s2.window == 100
    s1.freq = 200
    assert s1.window == 200
    # the other instance kept its cached value
    assert s2.window == 100
    assert counter(s2)["window"] == 1
    # explicit assignment on one instance does not affect the other
    s2.window = 7
    s2.freq = 5
    assert s2.window == 7
    s1.duration = 2.0
    assert s1.window == 400


def test_observer_sees_old_new_on_dependency_change():
    s = Sampler()
    assert s.window == 100
    changes = []
    s.observe(lambda change: changes.append(change), names=["freq"])
    s.freq = 200
    assert len(changes) == 1
    assert changes[0].old == 100
    assert changes[0].new == 200
    assert changes[0].name == "freq"


def test_invalidation_emits_no_change_event():
    s = Sampler()
    assert s.window == 100
    changes = []
    s.observe(lambda change: changes.append(change), names=["window"], type="change")
    s.freq = 200
    # invalidation is silent; the stale value is simply dropped
    assert changes == []
    # explicit assignment still notifies as usual; since the invalidated
    # cache held no value, ``old`` falls back to the static default,
    # exactly as when assigning a never-computed trait
    s.window = 5
    assert len(changes) == 1
    assert changes[0].old == 0
    assert changes[0].new == 5


def test_recompute_emits_default_event():
    s = Sampler()
    events = []
    s.observe(lambda event: events.append(event), names=["window"], type="default")
    assert s.window == 100
    s.freq = 200
    assert s.window == 200
    assert [event.value for event in events] == [100, 200]
    assert all(event.type == "default" for event in events)


def test_hold_trait_notifications_merges_events():
    s = Sampler()
    assert s.window == 100
    changes = []
    s.observe(lambda change: changes.append(change), names=["freq"])
    with s.hold_trait_notifications():
        s.freq = 200
        s.freq = 300
        # invalidation already happened inside the context
        assert not s.trait_has_value("window")
    # the two changes were compressed into a single notification
    assert len(changes) == 1
    assert changes[0].old == 100
    assert changes[0].new == 300
    # and the dependent default is recomputed once, lazily
    assert s.window == 300
    assert counter(s)["window"] == 2


def test_explicit_set_inside_hold_is_pinned():
    s = Sampler()
    with s.hold_trait_notifications():
        s.freq = 500
        s.window = 1
    s.freq = 600
    assert s.window == 1
    assert counter(s)["window"] == 0


def test_failed_default_is_not_cached():
    class Flaky(HasTraits):
        a = Int(1)
        b = Int()

        @default("b", depends_on=["a"])
        def _b_default(self):
            counter(self)["b"] += 1
            if counter(self)["b"] == 1:
                raise RuntimeError("boom")
            return self.a * 10

    f = Flaky()
    with pytest.raises(RuntimeError, match="boom"):
        f.b
    # the failed computation did not store a half-computed value
    assert not f.trait_has_value("b")
    # the next access retries the computation
    assert f.b == 10
    assert counter(f)["b"] == 2
    # invalidation still works afterwards
    f.a = 2
    assert f.b == 20
    assert counter(f)["b"] == 3


def test_type_validation_runs_on_recompute():
    class Bad(HasTraits):
        a = Int(1)
        b = Int()

        @default("b", depends_on=["a"])
        def _b_default(self):
            return "not an int"

    b = Bad()
    with pytest.raises(TraitError):
        b.b
    # a failed validation does not cache anything either
    assert not b.trait_has_value("b")


def test_add_traits_with_dependencies():
    s = Sampler()
    s.add_traits(extra=Int().tag(default_depends_on=["freq"]))
    assert s.extra == 0
    assert s.trait_has_value("extra")
    s.freq = 5
    # the dynamically added trait was invalidated
    assert not s.trait_has_value("extra")
    # explicit assignment pins it
    s.extra = 42
    s.freq = 6
    assert s.extra == 42


def test_add_traits_with_unknown_dependency():
    s = Sampler()
    with pytest.raises(TraitError, match="not a trait"):
        s.add_traits(bogus=Int().tag(default_depends_on=["missing"]))


def test_unknown_dependency_name():
    with pytest.raises(TraitError, match=r"'missing'.*not a trait"):

        class UnknownDep(HasTraits):
            a = Int()

            @default("a", depends_on=["missing"])
            def _a_default(self):
                return 1


def test_dependencies_for_unknown_trait():
    with pytest.raises(TraitError, match=r"'ghost'.*not a trait"):

        class UnknownTarget(HasTraits):
            a = Int()

            @default("ghost", depends_on=["a"])
            def _ghost_default(self):
                return 1


def test_non_string_dependency():
    with pytest.raises(TypeError, match="must be strings"):
        default("a", depends_on=[1])


def test_self_cycle():
    with pytest.raises(TraitError, match="a -> a"):

        class SelfCycle(HasTraits):
            a = Int()

            @default("a", depends_on=["a"])
            def _a_default(self):
                return 1


def test_two_cycle():
    with pytest.raises(TraitError, match="a -> b -> a"):

        class TwoCycle(HasTraits):
            a = Int()
            b = Int()

            @default("a", depends_on=["b"])
            def _a_default(self):
                return 1

            @default("b", depends_on=["a"])
            def _b_default(self):
                return 1


def test_three_cycle_reports_path():
    with pytest.raises(TraitError, match="a -> b -> c -> a"):

        class ThreeCycle(HasTraits):
            a = Int()
            b = Int()
            c = Int()

            @default("a", depends_on=["b"])
            def _a_default(self):
                return 1

            @default("b", depends_on=["c"])
            def _b_default(self):
                return 1

            @default("c", depends_on=["a"])
            def _c_default(self):
                return 1


def test_deep_graph_does_not_overflow_the_stack():
    depth = 2000
    attrs = {"d0": Int(0)}
    for i in range(1, depth):
        attrs[f"d{i}"] = Int().tag(default_depends_on=[f"d{i - 1}"])
    Deep = type("Deep", (HasTraits,), attrs)
    d = Deep()
    midpoint = f"d{depth // 2}"
    getattr(d, midpoint)
    assert d.trait_has_value(midpoint)
    d.d0 = 1
    # invalidation propagated iteratively through the whole chain
    assert not d.trait_has_value(midpoint)


def test_deep_cycle_raises_trait_error_not_recursion_error():
    depth = 2000
    attrs = {}
    for i in range(depth):
        attrs[f"d{i}"] = Int().tag(default_depends_on=[f"d{(i + 1) % depth}"])
    with pytest.raises(TraitError, match="circular dependency"):
        type("DeepCycle", (HasTraits,), attrs)
