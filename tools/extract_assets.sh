#!/usr/bin/env bash

# SPDX-FileCopyrightText: © 2024 ZeldaRET
# SPDX-License-Identifier: CC0-1.0

if [ $# -eq 0 ]
then
    echo "Usage: $0 <version> [args...]"
    exit 1
fi

v=$1
# Assets are always extracted from the n64-us baserom
.venv/bin/python3 -m tools.assets.extract extracted/n64-us/baserom extracted/$v -v n64-us "${@:2}"
