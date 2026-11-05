import { AGENTCORE_TARGET_TAG, AgentCoreApplication, type AgentCoreApplicationProps } from '@aws/agentcore-cdk';
import { CfnOutput, CfnResource, Duration, Fn, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import * as fs from 'node:fs';
import * as path from 'node:path';

export interface AgentCoreStackProps extends StackProps {
  /** Props for the AgentCore application, produced by transformAgentCoreJson from agentcore/agentcore.json. */
  application: AgentCoreApplicationProps;
}

/** Nombres lógicos declarados en agentcore/agentcore.json. */
const FINANCE_GATEWAY = 'finance';
const FINANCE_TOOLS_TARGET = 'finance-tools';
const PORTFOLIO_HARNESS = 'portfolio';
const TRADING_RUNTIME = 'trading_agent';
const POLICY_ENGINE = 'trading_guardrails';
const EVALUATORS = ['Builtin.GoalSuccessRate', 'Builtin.ToolSelectionAccuracy', 'Builtin.Helpfulness'];
/** Busca app/policies subiendo desde este archivo (funciona desde lib/ o dist/lib/). */
function findPoliciesDir(start: string): string {
  for (let dir = start; dir !== path.dirname(dir); dir = path.dirname(dir)) {
    const candidate = path.join(dir, 'app', 'policies');
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error('No se encontró app/policies en el proyecto.');
}

/** Ambientes que conservan datos al eliminar el stack. */
const PROTECTED_TARGETS = new Set(['prod']);

/**
 * The stack the AgentCore CLI deploys. Everything declared in agentcore/agentcore.json is
 * created by the AgentCoreApplication construct. Lo que sigue añade el estado compartido del
 * portafolio y conecta el Gateway con el Harness. Cada bloque se activa solo cuando la capa
 * correspondiente existe en agentcore.json, así que el mismo código sirve para todas las capas.
 */
export class AgentCoreStack extends Stack {
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);
    this.application = new AgentCoreApplication(this, 'Application', props.application);

    const target = props.tags?.[AGENTCORE_TARGET_TAG] ?? 'default';
    const isProtected = PROTECTED_TARGETS.has(target);

    // Capa 2: estado compartido del portafolio para la Lambda del Gateway.
    const toolsFunction = this.application.toolLambdaFunctions.get(FINANCE_TOOLS_TARGET);
    let table: dynamodb.Table | undefined;
    if (toolsFunction) {
      table = new dynamodb.Table(this, 'PortfolioTable', {
        partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
        sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: isProtected },
        deletionProtection: isProtected,
        removalPolicy: isProtected ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
      });
      table.grantReadWriteData(toolsFunction);
      toolsFunction.addEnvironment('PORTFOLIO_TABLE', table.tableName);
      toolsFunction.addEnvironment('PORTFOLIO_ID', 'demo');
      // Trazas de la Lambda en X-Ray / CloudWatch junto con las del Gateway.
      (toolsFunction.node.defaultChild as lambda.CfnFunction).tracingConfig = { mode: 'Active' };
      toolsFunction.addToRolePolicy(
        new iam.PolicyStatement({
          actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords'],
          resources: ['*'],
        }),
      );
      new CfnOutput(this, 'PortfolioTableName', { value: table.tableName });
    }

    // Capa 2: el Harness usa el Gateway del mismo stack, sin ARN fijo por ambiente.
    const gateway = this.application.gateways.get(FINANCE_GATEWAY);
    const harness = this.application.harnesses.get(PORTFOLIO_HARNESS);
    if (gateway && harness) {
      const cfnHarness = harness.node.findChild('Resource') as bedrockagentcore.CfnHarness;
      const yamlTools = (Array.isArray(cfnHarness.tools) ? cfnHarness.tools : []) as bedrockagentcore.CfnHarness.HarnessToolProperty[];
      cfnHarness.tools = [
        ...yamlTools.filter(tool => tool.name !== FINANCE_GATEWAY),
        {
          type: 'agentcore_gateway',
          name: FINANCE_GATEWAY,
          config: { agentCoreGateway: { gatewayArn: gateway.gatewayArn, outboundAuth: { awsIam: {} } } },
        },
      ];
    }

    // Capa 4: Identity. El Trading Agent solo acepta JWT de usuarios de Cognito; el agente
    // obtiene el usuario del token (no del payload). Se activa con el contexto alphainvest:identity.
    const runtimeForIdentity = this.application.runtimes.get(TRADING_RUNTIME);
    if (this.node.tryGetContext('alphainvest:identity') === true && runtimeForIdentity) {
      const userPool = new cognito.UserPool(this, 'InvestorsUserPool', {
        userPoolName: `AlphaInvest-${target}-investors`,
        selfSignUpEnabled: false,
        signInAliases: { username: true, email: true },
        passwordPolicy: { minLength: 12, requireDigits: true, requireSymbols: true },
        accountRecovery: cognito.AccountRecovery.NONE,
        removalPolicy: isProtected ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
        deletionProtection: isProtected,
      });
      const client = userPool.addClient('TradingAppClient', {
        userPoolClientName: 'alphainvest-trading-app',
        generateSecret: false,
        authFlows: { userPassword: true },
        accessTokenValidity: Duration.minutes(60),
        idTokenValidity: Duration.minutes(60),
        refreshTokenValidity: Duration.days(1),
        preventUserExistenceErrors: true,
      });
      const cfnRuntime = runtimeForIdentity.resource.node.defaultChild as CfnResource;
      cfnRuntime.addPropertyOverride('AuthorizerConfiguration.CustomJWTAuthorizer', {
        DiscoveryUrl: `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`,
        AllowedClients: [client.userPoolClientId],
      });
      new CfnOutput(this, 'InvestorsUserPoolId', { value: userPool.userPoolId });
      new CfnOutput(this, 'TradingAppClientId', { value: client.userPoolClientId });
    }

    // Capa 6: AlphaTrader Web (terminal ficticia de solo lectura) + Browser propio con grabación.
    // Se activa con el contexto alphainvest:browser.
    if (this.node.tryGetContext('alphainvest:browser') === true && table && harness) {
      const terminal = new lambda.Function(this, 'AlphaTraderWeb', {
        functionName: `AlphaInvest-${target}-alphatrader-web`,
        description: 'Terminal de trading ficticia (solo lectura) para la demo de AgentCore Browser',
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: 'web_terminal.handler',
        code: lambda.Code.fromAsset(path.join(path.dirname(findPoliciesDir(__dirname)), 'finance_tools'), {
          exclude: ['__pycache__', '*.pyc', 'tool_definitions.json', 'pyproject.toml'],
        }),
        memorySize: 256,
        timeout: Duration.seconds(10),
        environment: { PORTFOLIO_TABLE: table.tableName, PORTFOLIO_ID: 'demo' },
      });
      table.grantReadData(terminal);
      // URL pública a propósito: el navegador administrado debe alcanzarla. Solo HTML ficticio, solo GET.
      const terminalUrl = terminal.addFunctionUrl({ authType: lambda.FunctionUrlAuthType.NONE });

      const recordings = new s3.Bucket(this, 'BrowserRecordings', {
        encryption: s3.BucketEncryption.S3_MANAGED,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        lifecycleRules: [{ expiration: Duration.days(7) }],
        removalPolicy: isProtected ? RemovalPolicy.RETAIN : RemovalPolicy.DESTROY,
        autoDeleteObjects: !isProtected,
      });
      const browserRole = new iam.Role(this, 'BrowserRecordingRole', {
        assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
          conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
        }),
        description: 'Escribe las grabaciones de sesión de AgentCore Browser',
      });
      recordings.grantReadWrite(browserRole);
      browserRole.addToPolicy(new iam.PolicyStatement({
        actions: ['s3:ListMultipartUploadParts', 's3:AbortMultipartUpload'],
        resources: [recordings.arnForObjects('*')],
      }));
      browserRole.addToPolicy(new iam.PolicyStatement({
        actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams'],
        resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
      }));

      const browser = new bedrockagentcore.CfnBrowserCustom(this, 'TerminalBrowser', {
        name: `AlphaInvest_${target.replace(/-/g, '_')}_terminal_browser`,
        description: 'Navegador administrado con grabación para AlphaTrader Web',
        networkConfiguration: { networkMode: 'PUBLIC' },
        executionRoleArn: browserRole.roleArn,
        recordingConfig: { enabled: true, s3Location: { bucket: recordings.bucketName, prefix: 'recordings' } },
      });
      browser.node.addDependency(browserRole);

      harness.addToPolicy(new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:StartBrowserSession', 'bedrock-agentcore:StopBrowserSession',
          'bedrock-agentcore:GetBrowserSession', 'bedrock-agentcore:ListBrowserSessions',
          'bedrock-agentcore:UpdateBrowserStream', 'bedrock-agentcore:ConnectBrowserAutomationStream',
          'bedrock-agentcore:ConnectBrowserLiveViewStream',
        ],
        resources: [browser.attrBrowserArn],
      }));
      const cfnHarness = harness.node.findChild('Resource') as bedrockagentcore.CfnHarness;
      const currentTools = (Array.isArray(cfnHarness.tools) ? cfnHarness.tools : []) as bedrockagentcore.CfnHarness.HarnessToolProperty[];
      cfnHarness.tools = [
        ...currentTools.filter(tool => tool.type !== 'agentcore_browser'),
        { type: 'agentcore_browser', name: 'browser', config: { agentCoreBrowser: { browserArn: browser.attrBrowserArn } } },
      ];
      new CfnOutput(this, 'AlphaTraderWebUrl', { value: terminalUrl.url });
      new CfnOutput(this, 'TerminalBrowserId', { value: browser.attrBrowserId });
      new CfnOutput(this, 'BrowserRecordingsBucket', { value: recordings.bucketName });
    }

    // Capa 8: evaluación en línea del Portfolio Agent (Harness). El Runtime se evalúa con la
    // configuración declarada en agentcore.json (agentcore add online-eval). Se activa con
    // el contexto alphainvest:evaluations.
    if (this.node.tryGetContext('alphainvest:evaluations') === true && harness) {
      const harnessRuntimeId = Fn.select(1, Fn.split('/', harness.agentRuntimeArn));
      new bedrockagentcore.OnlineEvaluationConfig(this, 'PortfolioOnlineEval', {
        onlineEvaluationConfigName: `AlphaInvest_${target.replace(/-/g, '_')}_portfolio_quality`,
        description: 'Calidad del Portfolio Agent: objetivo, herramientas y utilidad',
        dataSource: bedrockagentcore.DataSourceConfig.fromCloudWatchLogs({
          logGroupNames: [`/aws/bedrock-agentcore/runtimes/${harnessRuntimeId}-DEFAULT`],
          serviceNames: [`harness_${harness.harnessName}.DEFAULT`],
        }),
        evaluators: EVALUATORS.map(id => bedrockagentcore.EvaluatorSelector.builtin(new bedrockagentcore.BuiltinEvaluator(id))),
        // En prod se muestrea una fracción del tráfico para controlar costos.
        samplingPercentage: isProtected ? 10 : 100,
        sessionTimeout: Duration.minutes(5),
        // El constructor L2 la crea deshabilitada si no se indica.
        executionStatus: bedrockagentcore.ExecutionStatus.ENABLED,
      });
    }

    // Capa 5: políticas Cedar con el ARN del Gateway y los roles reales de cada ambiente.
    const policyEngine = this.application.policyEngines.get(POLICY_ENGINE);
    const runtime = this.application.runtimes.get(TRADING_RUNTIME);
    if (policyEngine && gateway && runtime && harness) {
      const assumedRole = (roleName: string) => `arn:${this.partition}:sts::${this.account}:assumed-role/${roleName}`;
      const variables = {
        GatewayArn: gateway.gatewayArn,
        TradingAgentRole: assumedRole(runtime.role.roleName),
        PortfolioAgentRole: assumedRole(harness.role.roleName),
      };
      const policiesDir = findPoliciesDir(__dirname);
      for (const file of fs.readdirSync(policiesDir).filter(f => f.endsWith('.cedar')).sort()) {
        const name = path.basename(file, '.cedar');
        const template = fs.readFileSync(path.join(policiesDir, file), 'utf-8');
        new bedrockagentcore.CfnPolicy(this, `Policy-${name}`, {
          name,
          policyEngineId: policyEngine.policyEngineId,
          definition: { policy: { statement: Fn.sub(template, variables) } },
          validationMode: 'FAIL_ON_ANY_FINDINGS',
        });
      }
    }
  }
}
