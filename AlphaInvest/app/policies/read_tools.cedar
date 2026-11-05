// Ambos agentes pueden consultar precios, portafolio e historial.
// ${GatewayArn}, ${TradingAgentRole} y ${PortfolioAgentRole} los sustituye cdk-stack.ts por ambiente.
permit(
  principal is AgentCore::IamEntity,
  action in [
    AgentCore::Action::"finance-tools___get_stock_price",
    AgentCore::Action::"finance-tools___get_portfolio",
    AgentCore::Action::"finance-tools___get_trade_history"
  ],
  resource == AgentCore::Gateway::"${GatewayArn}"
)
when {
  principal.id == "${TradingAgentRole}" || principal.id == "${PortfolioAgentRole}"
};
