permit (
    principal is AgentCore::IamEntity,
    action == AgentCore::Action::"BrokerTools___get_runbook_procedure",
    resource == AgentCore::Gateway::"${GatewayArn}"
)
when {
    context.input.service_id == "claims-api" &&
    (context.input.alarm_code == "HIGH_ERROR_RATE" ||
     context.input.alarm_code == "DATABASE_CORRUPTION")
};

permit (
    principal is AgentCore::IamEntity,
    action == AgentCore::Action::"BrokerTools___inspect_service",
    resource == AgentCore::Gateway::"${GatewayArn}"
)
when {
    context.input.service_id == "claims-api" &&
    context.input.environment == "demo"
};

permit (
    principal is AgentCore::IamEntity,
    action == AgentCore::Action::"BrokerTools___execute_remediation",
    resource == AgentCore::Gateway::"${GatewayArn}"
)
when {
    context.input.service_id == "claims-api" &&
    context.input.environment == "demo" &&
    context.input.action == "restart_worker_pool"
};
