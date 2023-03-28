Contributing
============

We welcome contributions via GitHub pull requests:

- `Fork a Repo <https://help.github.com/articles/fork-a-repo/>`_
- `Creating a pull request
  <https://help.github.com/articles/creating-a-pull-request/>`_

Developing
----------

Prepare a developer environment.  Then, from the telnetlib3 code folder::

    pip install --editable .

Any changes made in this project folder are then made available to the python
interpreter as the 'telnetlib3' module irregardless of the current working
directory.

Running Tests
-------------

`Py.test <https://pytest.org>` is the test runner. Install and run tox

::

    pip install --upgrade tox
    tox

A convenience target, 'develop' is provided, which adds `-vv` and `--looponfail`
arguments, where the tests automatically re-trigger on any file change::

    tox -e develop

Code Formatting
---------------

To make code formatting easy on developers, and to simplify the conversation
around pull request reviews, this project has adopted the `black <https://github.com/psf/black/>`_
code formatter. This formatter must be run against any new code written for this
project. The advantage is, you no longer have to think about how your code is
styled; it's all handled for you!

To make this even easier on you, you can set up most editors to auto-run
``black`` for you. We have also set up a `pre-commit <https://pre-commit.com/>`_
hook to run automatically on every commit, with just a small bit of extra setup:

::

    pip install pre-commit
    pre-commit install --install-hooks

Now, before each git commit is accepted, this hook will run to ensure the code
has been properly formatted by ``black``.


Style and Static Analysis
-------------------------

All standards enforced by the underlying tools are adhered to by this project,
with the declarative exception of those found in `landscape.yml
<https://github.com/jquast/telnetlib3/blob/master/.landscape.yml>`_, or inline
using ``pylint: disable=`` directives.

Perform static analysis using tox target *sa*::

    tox -esa
