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
        self.assertEqual("LOG_ONLY", gateway["PolicyEngineConfiguration"]["Mode"])

    def test_policy_waits_for_gateway_tool_schema(self) -> None:
        policy = self.template["Resources"]["BrokerPolicy"]

        self.assertEqual("BrokerToolTarget", policy["DependsOn"])

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


if __name__ == "__main__":
    unittest.main()
