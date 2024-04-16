#!/bin/bash
# Copyright (c) 2024. Massachusetts Institute of Technology
# Notwithstanding any copyright notice, U.S. Government rights in this work are
# defined by DFARS 252.227-7013 or DFARS 252.227-7014 as detailed below. Use of
# this work other than as specifically authorized by the U.S. Government may
# violate any copyrights that exist in this work.
#
# UNLIMITED RIGHTS DFARS Clause reference: 252.227-7013 (a)(16) and
# 252.227-7014 (a)(16) Unlimited Rights. The Government has the right to use,
# modify, reproduce, perform, display, release or disclose this (technical data
# or computer software) in whole or in part, in any manner, and for any purpose
# whatsoever, and to have or authorize others to do so.
#
# THE SOFTWARE IS PROVIDED TO YOU ON AN "AS IS" BASIS.

echo "[INFO] $0 $*"

# Defaults:
CONFIG_PORT="--port 9000"
CONFIG_WORKERS="--workers 10"

# Overrides:
while [ $# -gt 1 ]
do
if [ "$1" = "--port" ]
then
   shift;
   CONFIG_PORT="--port $1"
   shift;
fi
if [ "$1" = "--workers" ]
then
   shift;
   CONFIG_WORKERS="--workers $1"
   shift;
fi
done


export CONF_LISTEN_HOST="--host 0.0.0.0"
export CONF_PROXY_HEADERS="--proxy-headers"
export CONF_ALLOW_IPS="--forwarded-allow-ips '*'"

export PYTHONPATH=$PYTHONPATH:/usr/local/lib/python3.11/site-packages/neurotrale-precomputed

echo "[INFO] uvicorn $CONF_LISTEN_HOST $CONF_PROXY_HEADERS $CONF_ALLOW_IPS $CONFIG_PORT $CONFIG_WORKERS neurotrale_precomputed_service:app"
uvicorn $CONF_LISTEN_HOST $CONF_PROXY_HEADERS $CONF_ALLOW_IPS $CONFIG_PORT $CONFIG_WORKERS neurotrale_precomputed_service:app
