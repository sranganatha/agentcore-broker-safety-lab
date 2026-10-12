import json
import unittest

from agentcore_broker_safety_lab.validate_fixtures import ROOT, validate_gateway_contract


class InfrastructureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = json.loads((ROOT / "infra" / "application.json").read_text(encoding="utf-8"))

    def test_gateway_contract_validates(self) -> None:
        validate_gateway_contract()

    def test_gateway_starts_in_policy_shadow_mode(self) -> None:
        gateway = self.template["Resources"]["BrokerGateway"]["Properties"]

        self.assertEqual("AWS_IAM", gateway["AuthorizerType"])
        self.assertEqual("MCP", gateway["ProtocolType"])
        self.assertEqual("LOG_ONLY", self.template["Parameters"]["PolicyEngineMode"]["Default"])
        self.assertEqual({"Ref": "PolicyEngineMode"}, gateway["PolicyEngineConfiguration"]["Mode"])

    def test_policy_waits_for_gateway_tool_schema(self) -> None:
        policies = [
            resource
            for resource in self.template["Resources"].values()
            if resource["Type"] == "AWS::BedrockAgentCore::Policy"
        ]

        self.assertEqual(3, len(policies))
        self.assertTrue(all(policy["DependsOn"] == "BrokerToolTarget" for policy in policies))

    def test_gateway_role_permissions_are_resource_scoped(self) -> None:
        policies = self.template["Resources"]["GatewayExecutionRole"]["Properties"]["Policies"]
        statements = [statement for policy in policies for statement in policy["PolicyDocument"]["Statement"]]

        self.assertTrue(statements)
        self.assertNotIn("*", {statement["Resource"] for statement in statements if isinstance(statement["Resource"], str)})

    def test_gateway_log_delivery_is_declared(self) -> None:
        resources = self.template["Resources"]

        self.assertEqual("AWS::Logs::LogGroup", resources["GatewayLogGroup"]["Type"])
        self.assertEqual("AWS::Logs::DeliverySource", resources["GatewayLogSource"]["Type"])
        self.assertEqual("AWS::Logs::DeliveryDestination", resources["GatewayLogDestination"]["Type"])
        self.assertEqual("AWS::Logs::Delivery", resources["GatewayLogDelivery"]["Type"])

    def test_stack_declares_target_and_ledger_boundaries(self) -> None:
        resources = self.template["Resources"]

        self.assertEqual("AWS::Lambda::Function", resources["ToolLambda"]["Type"])
        self.assertEqual("AWS::DynamoDB::Table", resources["ExecutionLedger"]["Type"])
        self.assertEqual(
            {"Fn::GetAtt": ["ToolLambda", "Arn"]},
            resources["BrokerToolTarget"]["Properties"]["TargetConfiguration"]["Mcp"]["Lambda"]["LambdaArn"],
        )
        self.assertEqual(
            [{"CredentialProviderType": "GATEWAY_IAM_ROLE"}],
            resources["BrokerToolTarget"]["Properties"]["CredentialProviderConfigurations"],
        )

    def test_runtime_is_direct_code_and_proposal_only(self) -> None:
        resources = self.template["Resources"]
        runtime = resources["BrokerRuntime"]["Properties"]

        self.assertEqual("PYTHON_3_12", runtime["AgentRuntimeArtifact"]["CodeConfiguration"]["Runtime"])
        self.assertEqual("HTTP", runtime["ProtocolConfiguration"])
        self.assertEqual({"NetworkMode": "PUBLIC"}, runtime["NetworkConfiguration"])

        policies = resources["BrokerRuntimeRole"]["Properties"]["Policies"]
        actions = {
            action
            for policy in policies
            for statement in policy["PolicyDocument"]["Statement"]
            for action in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
        }
        self.assertIn("bedrock-agentcore:InvokeGateway", actions)
        self.assertNotIn("dynamodb:PutItem", actions)
        self.assertNotIn("dynamodb:UpdateItem", actions)
        self.assertNotIn("lambda:InvokeFunction", actions)
        self.assertNotIn("bedrock-agentcore:CreateWorkloadIdentity", actions)

    def test_operator_role_is_narrow_and_separate_from_runtime(self) -> None:
        resources = self.template["Resources"]
        policies = resources["BrokerOperatorRole"]["Properties"]["Policies"]
        actions = {
            action
            for policy in policies
            for statement in policy["PolicyDocument"]["Statement"]
            for action in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
        }

        self.assertEqual(
            {"bedrock-agentcore:InvokeGateway", "dynamodb:GetItem", "dynamodb:UpdateItem"},
            actions,
        )
        self.assertNotIn("dynamodb:PutItem", actions)
        self.assertNotIn("bedrock-agentcore:CreatePolicy", actions)
        self.assertNotIn("lambda:InvokeFunction", actions)

        statements = [
            resource["Properties"]["Definition"]["Cedar"]["Statement"]["Fn::Sub"][0]
            for resource in resources.values()
            if resource["Type"] == "AWS::BedrockAgentCore::Policy"
        ]
        remediation = next(
            statement for statement in statements if "execute_remediation" in statement
        )
        self.assertIn('${OperatorPrincipal}', remediation)
        self.assertNotIn('${RuntimePrincipal}', remediation)


if __name__ == "__main__":
    unittest.main()
