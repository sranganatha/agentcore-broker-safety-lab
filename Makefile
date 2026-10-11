IMAGE ?= agentcore-broker-safety-lab:test

.PHONY: check test test-container

check:
	podman build --tag $(IMAGE) .

test:
	podman build --tag $(IMAGE) .
	podman run --rm --network none $(IMAGE)

test-container: test
