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


FROM python:3.11

# ARGs are cleared after 'FROM'.  Re-use proxy ARGs, declare new ones for this
# build stage.
ARG https_proxy
ARG http_proxy

ARG DEBIAN_FRONTEND="noninteractive"

RUN apt update && \
    mkdir /apps
COPY . /apps/
RUN cd /apps && \
    pip install .

COPY support-files/entrypoint.sh /apps/entrypoint.sh
RUN chmod 755 /apps/entrypoint.sh

ENTRYPOINT ["/apps/entrypoint.sh"]
