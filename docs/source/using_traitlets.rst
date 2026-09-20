Using Traitlets
===============

In short, traitlets let the user define classes that have

1. Attributes (traits) with type checking and dynamically computed
   default values
2. Traits emit change events when attributes are modified
3. Traitlets perform some validation and allow coercion of new trait
   values on assignment. They also allow the user to define custom
   validation logic for attributes based on the value of other
   attributes.

Default values, and checking type and value
-------------------------------------------

At its most basic, traitlets provides type checking, and dynamic default
value generation of attributes on :class:`traitlets.HasTraits`
subclasses:

.. code:: python

    from traitlets import HasTraits, Int, Unicode, default
    import getpass


    class Identity(HasTraits):
        username = Unicode()

        @default("username")
        def _default_username(self):
            return getpass.getuser()

.. code:: python

    class Foo(HasTraits):
        bar = Int()


    foo = Foo(bar="3")  # raises a TraitError

::

    TraitError: The 'bar' trait of a Foo instance must be an int,
    but a value of '3' <class 'str'> was specified

Dependent default values
------------------------

A dynamic default often derives its value from other traits, for instance a
sampling window computed from a frequency and a duration. By default,
traitlets cannot tell a cached derived value apart from an explicitly
assigned one, so later changes to the inputs are not reflected. Declaring
dependencies with ``depends_on`` opts into an invalidation protocol:

.. code:: python

    from traitlets import HasTraits, Int, Float, default


    class Sampler(HasTraits):
        freq = Int(100)
        duration = Float(1.0)
        window = Int()

        @default("window", depends_on=["freq", "duration"])
        def _window_default(self):
            return int(self.freq * self.duration)


    s = Sampler()
    s.window  # 100, computed lazily and cached
    s.freq = 200  # invalidates the cached default
    s.window  # 200, recomputed on access
    s.window = 42  # explicit assignment: the value is now pinned
    s.freq = 5
    s.window  # 42, dependency changes no longer apply
    del s.window  # deleting the explicit value restores the derived default
    s.window  # 5

Dependencies may also be declared with the ``default_depends_on`` metadata
key, e.g. ``window = Int().tag(default_depends_on=["freq", "duration"])``,
which combines with a plain ``@default("window")`` generator.

The semantics are designed to match the existing lazy behaviour of dynamic
defaults:

- **Derived vs. explicit.** A trait is in *derived* mode until it is
  explicitly assigned — directly, via a constructor keyword argument, or via
  config loading. Only derived values are invalidated by dependency changes;
  explicit values are never overwritten. Deleting a trait that declares
  dependencies drops the explicit value and restores derived mode.
- **Laziness.** Invalidation only drops the cached value; it never
  recomputes eagerly and emits no ``change`` notification. The default is
  recomputed on the next read, which emits the usual ``type="default"``
  notification, exactly as for the first computation. Validation behaves as
  it does for a first-time default: the trait type's own validation runs,
  while ``@validate`` cross-validators remain skipped under the
  cross-validation lock.
- **Propagation.** Invalidation propagates transitively along the declared
  dependency graph. Each affected trait is invalidated at most once per
  change, so diamond-shaped graphs invalidate their downstream traits a
  single time. Propagation stops at explicitly assigned traits, whose values
  — and therefore whose own dependents — remain valid.
- **Batched changes.** Inside :meth:`~.HasTraits.hold_trait_notifications`,
  invalidation applies immediately as each dependency is assigned, and since
  invalidation itself emits no events there is nothing extra to merge; the
  held ``change`` notifications of the dependencies are compressed as usual.
- **Inheritance.** Subclasses may declare dependencies for additional
  traits, or redeclare the dependencies of an inherited trait, in which case
  the subclass declaration replaces the parent's. Cached and explicit state
  is kept per instance and never shared.
- **Errors.** Declaring a dependency on a name that is not a trait of the
  class, or declaring a cycle of dependencies, raises a :exc:`TraitError`
  naming the offending path when the class is created. If a default
  computation raises, nothing is cached and the next access retries.

observe
-------

Traitlets implement the observer pattern

.. code:: python

    class Foo(HasTraits):
        bar = Int()
        baz = Unicode()


    foo = Foo()


    def func(change):
        print(change["old"])
        print(change["new"])  # as of traitlets 4.3, one should be able to
        # write print(change.new) instead


    foo.observe(func, names=["bar"])
    foo.bar = 1  # prints '0\n 1'
    foo.baz = "abc"  # prints nothing

When observers are methods of the class, a decorator syntax can be used.

.. code:: python

    class Foo(HasTraits):
        bar = Int()
        baz = Unicode()

        @observe("bar")
        def _observe_bar(self, change):
            print(change["old"])
            print(change["new"])

Validation and Coercion
-----------------------

Custom Cross-Validation
^^^^^^^^^^^^^^^^^^^^^^^

Each trait type (``Int``, ``Unicode``, ``Dict`` etc.) may have its own
validation or coercion logic. In addition, we can register custom
cross-validators that may depend on the state of other attributes.

Basic Example: Validating the Parity of a Trait
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code:: python

    from traitlets import HasTraits, TraitError, Int, Bool, validate


    class Parity(HasTraits):
        data = Int()
        parity = Int()

        @validate("data")
        def _valid_data(self, proposal):
            if proposal["value"] % 2 != self.parity:
                raise TraitError("data and parity should be consistent")
            return proposal["value"]

        @validate("parity")
        def _valid_parity(self, proposal):
            parity = proposal["value"]
            if parity not in [0, 1]:
                raise TraitError("parity should be 0 or 1")
            if self.data % 2 != parity:
                raise TraitError("data and parity should be consistent")
            return proposal["value"]


    parity_check = Parity(data=2)

    # Changing required parity and value together while holding cross validation
    with parity_check.hold_trait_notifications():
        parity_check.data = 1
        parity_check.parity = 1

Notice how all of the examples above return
``proposal['value']``. Returning a value
is necessary for validation to work
properly, since the new value of the trait will be the
return value of the function decorated by ``@validate``. If this
function does not have any ``return`` statement, then the returned
value will be ``None``, instead of what we wanted (which is ``proposal['value']``).

However, we recommend that custom cross-validators don't modify the state of
the HasTraits instance.

Advanced Example: Validating the Schema
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``List`` and ``Dict`` trait types allow the validation of nested
properties.

.. code:: python

    from traitlets import HasTraits, Dict, Bool, Unicode


    class Nested(HasTraits):
        value = Dict(
            per_key_traits={"configuration": Dict(value_trait=Unicode()), "flag": Bool()}
        )


    n = Nested()
    n.value = dict(flag=True, configuration={})  # OK
    n.value = dict(flag=True, configuration="")  # raises a TraitError.


However, for deeply nested properties it might be more appropriate to use an
external validator:

.. code:: python

    import jsonschema

    value_schema = {
        "type": "object",
        "properties": {
            "price": {"type": "number"},
            "name": {"type": "string"},
        },
    }

    from traitlets import HasTraits, Dict, TraitError, validate, default


    class Schema(HasTraits):
        value = Dict()

        @default("value")
        def _default_value(self):
            return dict(name="", price=1)

        @validate("value")
        def _validate_value(self, proposal):
            try:
                jsonschema.validate(proposal["value"], value_schema)
            except jsonschema.ValidationError as e:
                raise TraitError(e)
            return proposal["value"]


    s = Schema()
    s.value = dict(name="", price="1")  # raises a TraitError


Holding Trait Cross-Validation and Notifications
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Sometimes it may be impossible to transition between valid states for a
``HasTraits`` instance by changing attributes one by one. The
``hold_trait_notifications`` context manager can be used to hold the custom
cross validation until the context manager is released. If a validation error
occurs, changes are rolled back to the initial state.

Custom Events
-------------

Finally, trait types can emit other events types than trait changes. This
capability was added so as to enable notifications on change of values in
container classes. The items available in the dictionary passed to the observer
registered with ``observe`` depends on the event type.
