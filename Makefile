IMAGE ?= agentcore-broker-safety-lab:test
TOOLS_IMAGE ?= agentcore-broker-safety-lab:tools
RUNTIME_PACKAGE_IMAGE ?= agentcore-broker-safety-lab:runtime-package
AWS_DIR ?= $(if $(USERPROFILE),$(USERPROFILE)/.aws,$(HOME)/.aws)
AWS_REGION ?= us-east-1
AWS_PROFILE ?= default
APPLICATION_STACK ?= agentcore-broker-safety-lab
BOOTSTRAP_STACK ?= agentcore-broker-safety-lab-bootstrap

.PHONY: approve-aws check cloud-image demo deploy-aws destroy-aws package-aws policy-enforce policy-shadow test test-container verify-approval-aws verify-aws verify-aws-enforce verify-runtime-aws

check:
	podman build --file Containerfile.tools --tag $(TOOLS_IMAGE) .
	podman run --rm --network none $(TOOLS_IMAGE) python -m agentcore_broker_safety_lab.validate_fixtures
	podman run --rm --network none $(TOOLS_IMAGE)

test:
	podman build --tag $(IMAGE) .
	podman run --rm --network none $(IMAGE)

test-container: check test

demo:
	podman build --tag $(IMAGE) .
	podman run --rm --network none --volume "$(CURDIR):/workspace" $(IMAGE) python -m agentcore_broker_safety_lab.demo /workspace/artifacts

cloud-image:
	podman build --file Containerfile.tools --tag $(TOOLS_IMAGE) .

package-aws: cloud-image
	podman build --file Containerfile.runtime-package --tag $(RUNTIME_PACKAGE_IMAGE) .
	podman run --rm --network none --volume "$(CURDIR):/workspace" --workdir /workspace $(RUNTIME_PACKAGE_IMAGE)

deploy-aws: package-aws
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) -e CONFIRM_DEPLOY=$(CONFIRM_DEPLOY) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud deploy

verify-aws: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud verify --mode LOG_ONLY

policy-enforce: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) -e CONFIRM_POLICY_ENFORCE=$(CONFIRM_POLICY_ENFORCE) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud policy-mode --mode ENFORCE

policy-shadow: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud policy-mode --mode LOG_ONLY

verify-aws-enforce: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud verify --mode ENFORCE

verify-runtime-aws: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud verify-runtime

approve-aws: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) -e CONFIRM_APPROVE=$(CONFIRM_APPROVE) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud approve --task-id "$(TASK_ID)" --approval-request-id "$(APPROVAL_REQUEST_ID)"

verify-approval-aws: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud verify-approval

destroy-aws: cloud-image
	podman run --rm -e PYTHONUNBUFFERED=1 -e AWS_REGION=$(AWS_REGION) -e AWS_PROFILE=$(AWS_PROFILE) -e AWS_EC2_METADATA_DISABLED=true -e APPLICATION_STACK=$(APPLICATION_STACK) -e BOOTSTRAP_STACK=$(BOOTSTRAP_STACK) -e CONFIRM_DESTROY=$(CONFIRM_DESTROY) --volume "$(AWS_DIR):/root/.aws:ro" --volume "$(CURDIR):/workspace" --workdir /workspace $(TOOLS_IMAGE) python -m scripts.cloud destroy
