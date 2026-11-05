// Solo el Trading Agent puede operar, y como máximo 10 acciones por orden.
// El Portfolio Agent ni siquiera ve simulate_trade en tools/list.
permit(
  principal == AgentCore::IamEntity::"${TradingAgentRole}",
  action == AgentCore::Action::"finance-tools___simulate_trade",
  resource == AgentCore::Gateway::"${GatewayArn}"
)
when {
  context.input.quantity <= 10 &&
  ["BUY", "SELL"].contains(context.input.side)
};
