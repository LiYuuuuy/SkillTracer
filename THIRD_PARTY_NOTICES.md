# Third-party notices

## BrowserGym

`browsergym/core/` contains a fork of
[BrowserGym](https://github.com/ServiceNow/BrowserGym).
Copyright 2024 ServiceNow. Licensed under Apache-2.0.

The upstream copyright and license notice is preserved in
[`browsergym/core/NOTICE`](browsergym/core/NOTICE), with the full license in
[`browsergym/core/LICENSE`](browsergym/core/LICENSE).
Existing upstream author attribution remains in the package metadata.

The local version `0.10.2+skilltracer.2` includes SkillTracer changes for
navigation and observation timing, bounded browser timeouts, replacement and
verification of field input, and reporting task infeasibility.

## WebArena

`skilltracer/data/tasks/` contains 812 task definitions derived from
[WebArena's public task configuration](https://github.com/web-arena-x/webarena/blob/main/config_files/test.raw.json).
WebArena is licensed under Apache-2.0; its license is retained in
[`skilltracer/data/LICENSE`](skilltracer/data/LICENSE).

Task-specific names, addresses and account examples are benchmark data.
`skilltracer/browser/login.py` uses public benchmark test accounts for the
local WebArena websites. These are independent of provider API credentials.
Task origins are resolved through the configured `WA_*` settings.

Other installed dependencies retain their respective licenses. These notices
describe bundled third-party material and do not assign a license to
otherwise unlicensed project files.
