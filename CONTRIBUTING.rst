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

Install and run tox

::

    pip install --upgrade tox
    tox

`Py.test <https://pytest.org>` is the test runner. tox commands pass through
positional arguments, so you may for example use `looponfailing <https://pytest.org/latest/xdist.html#running-tests-in-looponfailing-mode>`
with python 3.5, stopping at the first failing test case::

    tox -epy35 -- -fx


Style and Static Analysis
-------------------------

All standards enforced by the underlying tools are adhered to by this project,
with the declarative exception of those found in `landscape.yml
<https://github.com/jquast/telnetlib3/blob/master/.landscape.yml>`_, or inline
using ``pylint: disable=`` directives.

Perform static analysis using tox target *sa*::

    tox -esa
