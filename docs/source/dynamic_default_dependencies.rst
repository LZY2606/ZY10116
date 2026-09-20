.. _dynamic_default_dependencies:

===============================
Dynamic Default Dependencies
===============================

.. py:currentmodule:: traitlets

A dynamic default (see :func:`default`) is evaluated lazily, on the first
attribute access, and the result is cached on the instance. Scientific
applications frequently derive a default from other traits -- a sampling
window, for instance, may be ``int(frequency * duration)``. Without further
information traitlets cannot tell, once those dependencies change, whether
the cached value is *still the derived default* or was *assigned explicitly*
by the user or by configuration.

The :func:`depends_on` decorator provides this information opt-in. Nothing
changes for defaults that do not use it.

Basic usage
===========

.. code-block:: python

    from traitlets import Float, HasTraits, Int, default, depends_on


    class Sampling(HasTraits):
        frequency = Float(10.0)
        duration = Float(1.0)
        window = Int()

        @default("window")
        @depends_on("frequency", "duration")
        def _window_default(self):
            return int(self.frequency * self.duration)

::

    >>> s = Sampling()
    >>> s.window                     # default evaluated once
    10
    >>> s.frequency = 20.0           # cached default is invalidated
    >>> s.window                     # lazily recomputed on access
    20

Rules:

* While the value has **not been explicitly assigned**, changing any
  declared dependency drops the cached value; the next access recomputes it.
* Once the trait is assigned explicitly -- through an attribute assignment,
  a constructor keyword argument, ``set_trait``, or configuration --
  dependency changes never overwrite it.
* ``del obj.window`` removes the explicit (or cached) value and restores the
  derived mode; the next access runs the default generator again.
* ``HasTraits.trait_is_default("window")`` reports whether the current value
  is a cached derived default, and ``HasTraits.default_dependencies("window")
  returns the declared dependency names.

The decorator may be stacked either above or below :func:`default`, and the
same dependencies can also be declared through trait metadata with
``Int().tag(depends_on=("frequency", "duration"))``.

Explicit versus derived values
==============================

::

    >>> s = Sampling()
    >>> s.window = 99               # explicit assignment wins
    >>> s.frequency = 30.0
    >>> s.window
    99
    >>> del s.window                # back to derived mode
    >>> s.window
    30

Configuration injection behaves like an explicit assignment: a value loaded
from a ``Config`` is protected against dependency changes, while a derived
trait still follows configuration values of its declared dependencies.

Inheritance
===========

A declaration in a subclass **replaces** the inherited declaration by
default. Pass ``extend=True`` to keep the inherited names and add more:

.. code-block:: python

    class Base(HasTraits):
        a = Int(1)
        x = Int()

        @default("x")
        @depends_on("a")
        def _x_default(self):
            return self.a


    class Extended(Base):
        b = Int(10)

        @depends_on("b", extend=True)
        def _x_default(self):
            return self.a + self.b

::

    >>> Extended.default_dependencies("x")
    ('a', 'b')

Multi-level and diamond graphs
==============================

Dependencies propagate topologically through transitive edges. In a diamond
(``d`` depends on ``b`` and ``c``, both depending on ``a``), changing ``a``
invalidates ``b``, ``c`` and ``d`` exactly once; each default generator runs
at most once when the tip is next read.

Evaluation of a missing derived value materializes its missing declared
dependencies iteratively in dependency order rather than through nested
descriptor calls, so dependency chains of any length cannot recurse into a
stack overflow.

A trait that holds an explicit value acts as a barrier: propagation stops at
that trait, so downstream derived values keep reading the frozen value.

Cycles
======

A declaration that (transitively) depends on itself is rejected at class
creation with a ``TraitError`` whose message contains the full cycle path,
for example ``x -> y -> x``.

Event and validation timing
===========================

The protocol follows traitlets' existing lazy semantics:

* **Invalidation is silent.** Dropping a cached value emits no change event
  and does not touch observers. Observers therefore never see a change to a
  value nobody has read.
* **Recomputation is lazy and notifies as before.** The next ``getattr``
  runs the default generator, caches the value, and emits the historical
  ``type="default"`` notification (which carries ``value`` but no ``old``).
  No ``change`` event is produced by the recomputation.
* **Validators run on explicit sets**, as always. A lazily materialized
  default is computed under the cross-validation lock, exactly like an
  ordinary dynamic default, so a ``@validate`` handler is not invoked merely
  because a derived value was recomputed.
* **``hold_trait_notifications``** keeps working: assignments inside the
  context invalidate immediately and are compressed per trait as usual; if a
  derived trait is read inside the context it is recomputed from the new
  dependencies, and its ``default`` event is delivered at that moment (the
  pre-existing behavior for dynamic defaults). If final cross validation
  fails, rollback restores the previous values and derived caches.

Failures and dynamic traits
===========================

* If the default generator raises, **nothing is cached**: the value stays
  absent and no ``default`` event is sent. The next access invokes the
  generator again, so transient failures can be retried after the dependency
  state changes.
* A dependency name that is not a trait of the class raises a clear
  ``TraitError`` the first time the dependency protocol is exercised on an
  instance.
* Defaults referring to traits added later with ``add_traits`` keep
  working: after the traits are added their pending declarations are
  resolved and the dependency graph is rebuilt.
* Cache and explicit-value state are stored strictly per instance; two
  instances never share caches, and the state survives pickling.
