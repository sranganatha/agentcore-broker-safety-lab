IMAGE ?= agentcore-broker-safety-lab:test
TOOLS_IMAGE ?= agentcore-broker-safety-lab:tools

.PHONY: check test test-container

check:
	podman build --file Containerfile.tools --tag $(TOOLS_IMAGE) .
	podman run --rm --network none $(TOOLS_IMAGE) python -m agentcore_broker_safety_lab.validate_fixtures
	podman run --rm --network none $(TOOLS_IMAGE)

test:
	podman build --tag $(IMAGE) .
	podman run --rm --network none $(IMAGE)

test-container: check test
