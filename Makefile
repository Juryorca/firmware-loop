# firmware-loop 核心框架入口
PY ?= python3
export PYTHONPATH := $(CURDIR)

.PHONY: help test demo run-demo migrate-real

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | column -t -s "$$(printf '\t')"

test:            ## 跑框架测试(不依赖本机 state/)
	$(PY) -m pytest tests/ -q

demo:            ## 建一个临时目标 + 假设 + 派生量(/tmp/fwdemo)
	rm -rf /tmp/fwdemo && mkdir -p /tmp/fwdemo
	$(PY) -m fwcore --root /tmp/fwdemo init demo --version 1.0
	$(PY) -m fwcore --root /tmp/fwdemo hyp add demo "示例假设"
	$(PY) -m fwcore --root /tmp/fwdemo derive demo

run-demo:        ## 用脚本化模型跑一次完整循环
	FW_TARGET=demo $(PY) -m fwcore --root /tmp/fwdemo run demo --steps 5

e2e-real:        ## 真实模型端到端(多轮工具调用 + 暂停恢复);需要 ~/.pi/agent/models.json 里的 key
	$(PY) scripts/e2e_real_model.py --steps 8

migrate-real:    ## 把某目标的 notes 迁到临时库并复核(不改动 state/):make migrate-real T=<目标>
	@test -n "$(T)" || (echo "用法: make migrate-real T=<目标目录名>" >&2; exit 2)
	rm -rf /tmp/fwmig && mkdir -p /tmp/fwmig/state/$(T)
	cp state/$(T)/notes.md /tmp/fwmig/state/$(T)/notes.md
	$(PY) -m fwcore --root /tmp/fwmig init $(T)
	$(PY) -m fwcore --root /tmp/fwmig migrate $(T)
