#!/bin/bash

TARGETS="omgmail"

if [ "$1" == "--check-only" ]; then
    RUFF_CHECK_ARGS=""
    RUFF_FORMAT_ARGS=" --check"
    DO_FORMAT=0
else
    RUFF_CHECK_ARGS=" --fix"
    RUFF_FORMAT_ARGS=""
    DO_FORMAT=1
fi

if [ ${DO_FORMAT} -eq 1 ]; then
    isort --force-single-line-imports ${TARGETS}
    autoflake --remove-all-unused-imports --recursive --remove-unused-variables --in-place ${TARGETS} --exclude=__init__.py
    black --fast ${TARGETS}
    isort ${TARGETS}
fi
ruff check ${TARGETS} ${RUFF_CHECK_ARGS} && ruff format ${TARGETS} ${RUFF_FORMAT_ARGS} && mypy ${TARGETS} && pytest .
