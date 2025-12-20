import axios from 'axios';
import https from 'https';
import express from 'express';
import type { Request, Response } from 'express';
import cors from 'cors';
import { randomUUID } from 'node:crypto';
import { z } from "zod";
import dotenv from 'dotenv';

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";

import { fetchMagentoApiEnvironment, fetchMagentoApiSchema, MagentoApiSchema, MagentoEnvironment } from './swagger.js';
import { callMagentoApi, CallApiParams } from './tools.js';

dotenv.config();

// Allow HTTP for localhost development
process.env.MCP_DANGEROUSLY_ALLOW_INSECURE_ISSUER_URL = 'true';

const MCP_TITLE = process.env.MCP_TITLE || 'Magento 2';
const PORT = parseInt(process.env.PORT || '3000', 10);
const LOG_LEVEL = (process.env.LOG_LEVEL || 'info').toLowerCase();
const BASE_URL = process.env.BASE_URL || `http://localhost:${PORT}`;
const OPENAI_APPS_CHALLENGE = process.env.OPENAI_APPS_CHALLENGE || 'demo-challenge-token';

const LEVELS: Record<string, number> = { error: 0, warn: 1, info: 2, debug: 3 };
function log(level: 'error' | 'warn' | 'info' | 'debug', ...args: any[]) {
    const current = LEVELS[LOG_LEVEL] ?? 2;
    if ((LEVELS[level] ?? 2) <= current) {
        const prefix = `[${new Date().toISOString()}] [${level.toUpperCase()}]`;
        if (level === 'debug') console.log(prefix, ...args);
        else if (level === 'info') console.info(prefix, ...args);
        else if (level === 'warn') console.warn(prefix, ...args);
        else console.error(prefix, ...args);
    }
}

// Parse featured APIs from env once
const featuredApis: {[k: string]: {description: string, target: string}} = {};
for (const [key, value] of Object.entries(process.env)) {
    if (value && value?.length > 0 && key.startsWith('FEATURED_APIS_') && !key.includes("DESCRIPTION")) {
        const parts = value.split('::');
        if (parts.length === 3) {
            const description = process.env[`${key}_DESCRIPTION`];
            featuredApis[parts[0]] = {
                description: description ?? parts[0],
                target: `${parts[1]}::${parts[2]}`
            };
        }
    }
}
log('debug', 'Featured APIs loaded', { count: Object.keys(featuredApis).length, names: Object.keys(featuredApis) });

// ============================================================================
// OAuth Storage - In-memory for demo, use Redis/DB in production
// ============================================================================

interface OAuthClient {
    client_id: string;
    client_secret?: string;
    redirect_uris: string[];
    client_name?: string;
    grant_types?: string[];
    response_types?: string[];
    scope?: string;
}

interface AuthorizationCode {
    code: string;
    clientId: string;
    redirectUri: string;
    codeChallenge?: string;
    codeChallengeMethod?: string;
    scopes: string[];
    state?: string;
    // Magento credentials collected during authorization
    magentoBaseUrl: string;
    magentoToken: string;
    expiresAt: number;
}

interface AccessToken {
    token: string;
    clientId: string;
    scopes: string[];
    magentoBaseUrl: string;
    magentoToken: string;
    expiresAt: number;
}

// Storage
const registeredClients = new Map<string, OAuthClient>();
const authorizationCodes = new Map<string, AuthorizationCode>();
const accessTokens = new Map<string, AccessToken>();

// ============================================================================
// Magento Context
// ============================================================================

const schemaCache = new Map<string, { schema: MagentoApiSchema, environment: MagentoEnvironment }>();

async function getMagentoContext(baseUrl: string, token: string) {
    const cacheKey = `${baseUrl}`;
    if (schemaCache.has(cacheKey)) {
        log('debug', 'Cache hit for schema/environment', { baseUrl });
        return schemaCache.get(cacheKey)!;
    }

    log('info', 'Fetching Magento schema/environment', { baseUrl });

    const axiosInstance = axios.create({
        baseURL: baseUrl + '/rest',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        httpsAgent: new https.Agent({ rejectUnauthorized: false }),
    });

    const schema = await fetchMagentoApiSchema(axiosInstance);
    const environment = await fetchMagentoApiEnvironment(axiosInstance);
    log('info', 'Fetched Magento schema', { baseUrl, paths: Object.keys(schema.paths || {}).length });
    
    const context = { schema, environment };
    schemaCache.set(cacheKey, context);
    return context;
}

function createAxiosInstance(baseUrl: string, token: string) {
    return axios.create({
        baseURL: baseUrl + '/rest',
        headers: {
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json',
        },
        httpsAgent: new https.Agent({ rejectUnauthorized: false }),
    });
}

// ============================================================================
// Tool Registration
// ============================================================================

function registerTools(server: McpServer, axiosInstance: any, schema: MagentoApiSchema, environment: MagentoEnvironment) {
    let added = 0;
    for (const [name, api] of Object.entries(featuredApis)) {
        const { description, target } = api;
        const [method, path] = target.split('::');

        if (!schema.paths[path] || !schema.paths[path][method as keyof typeof schema.paths[string]]) {
            log('warn', 'Skipping featured API: missing path/method', { name, path, method });
            continue;
        }

        let toolSignature: any = {
            storeCode: z.nullable(z.string()).default("all").describe('Store code (e.g. default'),
        };

        const op = schema.paths[path][method as keyof typeof schema.paths[string]] as any;
        for (const params of op.parameters ?? []) {
            if (params.in === 'path') {
                let toolParamSignature: any = params.type === 'integer' ? z.number() : z.string();
                toolParamSignature = params.required ? toolParamSignature : z.nullable(toolParamSignature);
                toolSignature[params.name] = toolParamSignature;
            } else if (params.in === 'query') {
                let toolParamSignature: any = z.string();
                toolParamSignature = params.required ? toolParamSignature : z.nullable(toolParamSignature).optional();
                toolSignature[params.name] = toolParamSignature;
            } else if (params.in === 'body') {
                if (params.schema && params.schema.properties) {
                    for (const [key, value] of Object.entries(params.schema.properties)) {
                        let toolParamSignature: any = (value as any)?.type === 'integer' ? z.number() : z.string();
                        toolParamSignature = params.schema.required?.includes(key) ? toolParamSignature : z.nullable(toolParamSignature).optional();
                        toolSignature[key] = toolParamSignature;
                    }
                }
            }
        }

        // Determine if this is a read-only or destructive operation
        const isReadOnly = method.toLowerCase() === 'get';
        const isDestructive = method.toLowerCase() === 'delete';
        
        server.registerTool(
            name,
            {
                description,
                inputSchema: toolSignature,
                annotations: {
                    title: name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
                    readOnlyHint: isReadOnly,
                    destructiveHint: isDestructive,
                    idempotentHint: isReadOnly || method.toLowerCase() === 'put',
                    openWorldHint: true // Interacts with external Magento API
                }
            },
            async (params: Record<string, any>) => {
                const callParams: CallApiParams = {
                    method: method.toLowerCase() as CallApiParams['method'],
                    storeCode: params.storeCode ?? null,
                    path: path,
                    query: null,
                    body: null,
                };
                const { storeCode, ...restParams } = params;

                if (method.toLowerCase() === 'get' || method.toLowerCase() === 'delete') {
                    const queryString = new URLSearchParams(restParams).toString();
                    callParams.query = queryString.length > 0 ? `?${queryString}` : null;
                } else {
                    const pathParamNames = (path.match(/{([^}]+)}/g) || []).map(p => p.replace(/{|}/g, ''));
                    const pathParams: Record<string, any> = {};
                    const otherParams: Record<string, any> = {};
                    for (const key in restParams) {
                        if (pathParamNames.includes(key)) {
                            pathParams[key] = restParams[key];
                        } else {
                            otherParams[key] = restParams[key];
                        }
                    }
                    const queryString = new URLSearchParams(pathParams).toString();
                    callParams.query = queryString.length > 0 ? `?${queryString}` : null;
                    callParams.body = otherParams;
                }

                return await callMagentoApi(axiosInstance, callParams);
            }
        );
        added += 1;
    }

    // Generic tools
    server.registerTool(
        "lvl0_info__get_deployment_info",
        {
            title: "Get Deployment Info",
            description: "Get deployment information including websites, store groups, and store views",
            annotations: {
                title: "Get Deployment Info",
                readOnlyHint: true,
                destructiveHint: false,
                idempotentHint: true,
                openWorldHint: true
            }
        },
        async () => ({
            content: [{ type: 'text', text: JSON.stringify({ environment, websites: environment.websites, stores: { storeGroups: environment.storeGroups, storeViews: environment.storeViews } }) }]
        })
    );

    server.registerTool(
        "lvl1_rest__get_api_definitions",
        {
            title: "Get API Definitions",
            description: "Get OpenAPI schema definitions including swagger info, host, basePath, schemes, and type definitions",
            annotations: {
                title: "Get API Definitions",
                readOnlyHint: true,
                destructiveHint: false,
                idempotentHint: true,
                openWorldHint: false // Returns cached schema data
            }
        },
        () => ({
            content: [{ type: 'text', text: JSON.stringify({ swagger: schema.swagger, info: schema.info, host: schema.host, basePath: schema.basePath, schemes: schema.schemes, definitions: schema.definitions, paths: {} }) }]
        })
    );

    server.registerTool(
        "lvl1_rest__search_api_methods",
        {
            title: "Search API Methods",
            description: "Search OpenAPI schema for API methods by keyword. Returns matching API paths and their details.",
            inputSchema: { search: z.nullable(z.string()).describe('Search terms to filter API methods (space-separated)') },
            annotations: {
                title: "Search API Methods",
                readOnlyHint: true,
                destructiveHint: false,
                idempotentHint: true,
                openWorldHint: false // Searches cached schema data
            }
        },
        ({ search }) => {
            let paths = schema.paths;
            if (search) {
                const terms = search.toLowerCase().split(' ').filter(t => t.length > 0);
                paths = Object.fromEntries(Object.entries(paths).filter(([k]) => terms.some(t => k.toLowerCase().includes(t))));
            }
            return { content: Object.entries(paths).map(([path, item]) => ({ type: 'text', text: JSON.stringify({ path, ...item }) })) as any };
        }
    );

    server.registerTool(
        "lvl1_rest__call_api_method",
        {
            title: "Call REST API Method",
            description: "Call any Magento 2 REST API method directly. Supports GET, POST, PUT, and DELETE operations.",
            inputSchema: {
                method: z.enum(['GET', 'get', 'POST', 'post', 'PUT', 'put', 'DELETE', 'delete']).describe('HTTP method'),
                storeCode: z.nullable(z.string()).default("all").describe('Store code (e.g. default, all)'),
                path: z.string().describe('API path (e.g. /V1/products)'),
                query: z.nullable(z.string()).optional().describe('Query string parameters'),
                body: z.nullable(z.record(z.string(), z.any())).optional().describe('Request body for POST/PUT requests'),
            },
            annotations: {
                title: "Call REST API Method",
                readOnlyHint: false, // Can perform any operation
                destructiveHint: true, // DELETE operations are destructive
                idempotentHint: false, // Depends on the method used
                openWorldHint: true // Interacts with external Magento API
            }
        },
        async (params: any) => await callMagentoApi(axiosInstance, { ...params, method: params.method.toLowerCase() })
    );

    log('info', 'Registered tools', { featuredApis: added, genericTools: 4, total: added + 4 });
}

// ============================================================================
// PKCE Verification
// ============================================================================

async function verifyCodeChallenge(verifier: string, challenge: string, method: string): Promise<boolean> {
    if (method === 'plain') {
        return verifier === challenge;
    }
    if (method === 'S256') {
        const encoder = new TextEncoder();
        const data = encoder.encode(verifier);
        const hashBuffer = await crypto.subtle.digest('SHA-256', data);
        const hashArray = new Uint8Array(hashBuffer);
        const hashBase64 = Buffer.from(hashArray).toString('base64url');
        return hashBase64 === challenge;
    }
    return false;
}

// ============================================================================
// Express App
// ============================================================================

const app = express();

// CORS - must allow all headers that MCP clients send
app.use(cors({
    origin: true, // Reflect the request origin
    credentials: true,
    methods: ['GET', 'POST', 'DELETE', 'OPTIONS', 'PUT', 'PATCH'],
    allowedHeaders: [
        'Content-Type', 
        'Authorization', 
        'Mcp-Session-Id',
        'X-Requested-With',
        'Accept',
        'Origin',
        'Cache-Control',
        'X-Request-Id',
        'mcp-session-id' // lowercase version
    ],
    exposedHeaders: [
        'Mcp-Session-Id',
        'mcp-session-id',
        'Content-Type'
    ],
    maxAge: 86400, // Cache preflight for 24 hours
}));

// Handle OPTIONS preflight for all routes
app.use(cors());

app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// ============================================================================
// OAuth 2.0 Discovery Metadata (RFC 8414)
// ============================================================================

app.get('/.well-known/openai-apps-challenge', (req, res) => {
    log('debug', 'OpenAI Apps challenge requested');
    res.type('text/plain').send(OPENAI_APPS_CHALLENGE);
});

app.get('/.well-known/oauth-authorization-server', (_req, res) => {
    log('debug', 'OAuth metadata requested');
    res.json({
        issuer: BASE_URL,
        authorization_endpoint: `${BASE_URL}/authorize`,
        token_endpoint: `${BASE_URL}/token`,
        registration_endpoint: `${BASE_URL}/register`,
        response_types_supported: ['code'],
        grant_types_supported: ['authorization_code', 'refresh_token'],
        code_challenge_methods_supported: ['S256', 'plain'],
        token_endpoint_auth_methods_supported: ['client_secret_post', 'none'],
        scopes_supported: ['mcp:tools'],
        service_documentation: 'https://github.com/neutrome-labs/magento2-admin-mcp-server-v2'
    });
});

// Protected Resource Metadata (for MCP)
app.get('/.well-known/oauth-protected-resource', (_req, res) => {
    res.json({
        resource: `${BASE_URL}/mcp`,
        authorization_servers: [BASE_URL],
        scopes_supported: ['mcp:tools']
    });
});

// ============================================================================
// Dynamic Client Registration (RFC 7591)
// ============================================================================

app.post('/register', (req, res) => {
    const { redirect_uris, client_name, grant_types, response_types, scope } = req.body;
    
    log('debug', 'Client registration request', { client_name, redirect_uris });
    
    if (!redirect_uris || !Array.isArray(redirect_uris) || redirect_uris.length === 0) {
        res.status(400).json({ error: 'invalid_request', error_description: 'redirect_uris required' });
        return;
    }

    const clientId = randomUUID();
    const clientSecret = randomUUID();
    
    const client: OAuthClient = {
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uris,
        client_name: client_name || 'MCP Client',
        grant_types: grant_types || ['authorization_code', 'refresh_token'],
        response_types: response_types || ['code'],
        scope: scope || 'mcp:tools'
    };
    
    registeredClients.set(clientId, client);
    log('info', 'Client registered', { clientId, client_name: client.client_name });
    
    res.status(201).json({
        client_id: clientId,
        client_secret: clientSecret,
        client_name: client.client_name,
        redirect_uris: client.redirect_uris,
        grant_types: client.grant_types,
        response_types: client.response_types,
        scope: client.scope
    });
});

// ============================================================================
// Authorization Endpoint - Shows form to collect Magento credentials
// ============================================================================

app.get('/authorize', (req: Request, res: Response) => {
    const { client_id, redirect_uri, response_type, code_challenge, code_challenge_method, state, scope } = req.query as Record<string, string>;
    
    log('debug', 'Authorization request', { client_id, redirect_uri, response_type, scope });
    
    // Validate required params
    if (!client_id || !redirect_uri || response_type !== 'code') {
        res.status(400).send('Missing required OAuth parameters');
        return;
    }
    
    // Validate client (allow unregistered clients for MCP Inspector compatibility)
    const client = registeredClients.get(client_id);
    if (client && !client.redirect_uris.includes(redirect_uri)) {
        res.status(400).send('Invalid redirect_uri');
        return;
    }

    // Show authorization form
    const html = `<!DOCTYPE html>
<html>
<head>
    <title>Connect to Magento - ${MCP_TITLE}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; justify-content: center; align-items: center; 
            min-height: 100vh; margin: 0; 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        .container {
            background: white; padding: 2rem; border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            width: 100%; max-width: 420px;
        }
        h1 { margin: 0 0 0.5rem; font-size: 1.5rem; color: #333; }
        .subtitle { color: #666; margin-bottom: 1.5rem; font-size: 0.9rem; }
        .field { margin-bottom: 1rem; }
        label { display: block; margin-bottom: 0.5rem; font-weight: 600; color: #333; font-size: 0.9rem; }
        input { 
            width: 100%; padding: 0.75rem; border: 2px solid #e1e5eb; 
            border-radius: 8px; font-size: 1rem; transition: border-color 0.2s;
        }
        input:focus { outline: none; border-color: #667eea; }
        button { 
            width: 100%; padding: 0.875rem; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white; border: none; border-radius: 8px; 
            font-size: 1rem; font-weight: 600; cursor: pointer;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4); }
        .help { font-size: 0.8rem; color: #888; margin-top: 0.25rem; }
        .client-info { background: #f8f9fa; padding: 0.75rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.8rem; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🛒 Connect to Magento</h1>
        <p class="subtitle">Enter your Magento store credentials to authorize access</p>
        
        <div class="client-info">
            Authorizing: <strong>${client?.client_name || 'MCP Client'}</strong>
        </div>
        
        <form method="POST" action="/authorize">
            <div class="field">
                <label for="baseUrl">Magento Store URL</label>
                <input type="url" id="baseUrl" name="baseUrl" placeholder="https://your-store.com" required />
                <div class="help">Your Magento 2 store base URL</div>
            </div>
            <div class="field">
                <label for="magentoToken">Integration Access Token</label>
                <input type="password" id="magentoToken" name="magentoToken" required />
                <div class="help">From System → Integrations in Magento Admin</div>
            </div>
            <input type="hidden" name="client_id" value="${client_id || ''}" />
            <input type="hidden" name="redirect_uri" value="${redirect_uri || ''}" />
            <input type="hidden" name="code_challenge" value="${code_challenge || ''}" />
            <input type="hidden" name="code_challenge_method" value="${code_challenge_method || ''}" />
            <input type="hidden" name="state" value="${state || ''}" />
            <input type="hidden" name="scope" value="${scope || ''}" />
            <button type="submit">Authorize Access</button>
        </form>
    </div>
</body>
</html>`;
    
    res.type('html').send(html);
});

app.post('/authorize', async (req: Request, res: Response) => {
    const { client_id, redirect_uri, code_challenge, code_challenge_method, state, scope, baseUrl, magentoToken } = req.body;
    
    log('debug', 'Authorization form submitted', { client_id, redirect_uri, baseUrl: baseUrl?.substring(0, 30) });
    
    if (!client_id || !redirect_uri || !baseUrl || !magentoToken) {
        res.status(400).send('Missing required parameters');
        return;
    }
    
    // Validate Magento credentials by trying to fetch the schema
    try {
        log('info', 'Validating Magento credentials', { baseUrl });
        await getMagentoContext(baseUrl, magentoToken);
        log('info', 'Magento credentials validated successfully');
    } catch (error: any) {
        log('error', 'Magento validation failed', { error: error.message });
        res.status(400).send(`
            <html><body style="font-family: sans-serif; padding: 2rem; text-align: center;">
                <h1>❌ Connection Failed</h1>
                <p>Could not connect to Magento: ${error.message}</p>
                <p><a href="javascript:history.back()">Go back and try again</a></p>
            </body></html>
        `);
        return;
    }
    
    // Generate authorization code
    const code = randomUUID();
    const authCode: AuthorizationCode = {
        code,
        clientId: client_id,
        redirectUri: redirect_uri,
        codeChallenge: code_challenge,
        codeChallengeMethod: code_challenge_method || 'plain',
        scopes: scope ? scope.split(' ') : ['mcp:tools'],
        state,
        magentoBaseUrl: baseUrl,
        magentoToken,
        expiresAt: Date.now() + 10 * 60 * 1000 // 10 minutes
    };
    
    authorizationCodes.set(code, authCode);
    log('info', 'Authorization code generated', { code: code.substring(0, 8) + '...' });
    
    // Redirect back to client
    const redirectUrl = new URL(redirect_uri);
    redirectUrl.searchParams.set('code', code);
    if (state) redirectUrl.searchParams.set('state', state);
    
    res.redirect(302, redirectUrl.toString());
});

// ============================================================================
// Token Endpoint
// ============================================================================

app.post('/token', async (req: Request, res: Response) => {
    const { grant_type, code, code_verifier, client_id, client_secret, redirect_uri } = req.body;
    
    log('debug', 'Token request', { grant_type, client_id, hasCode: !!code, hasVerifier: !!code_verifier });
    
    if (grant_type !== 'authorization_code') {
        res.status(400).json({ error: 'unsupported_grant_type' });
        return;
    }
    
    if (!code) {
        res.status(400).json({ error: 'invalid_request', error_description: 'code required' });
        return;
    }
    
    const authCode = authorizationCodes.get(code);
    if (!authCode) {
        res.status(400).json({ error: 'invalid_grant', error_description: 'Invalid or expired code' });
        return;
    }
    
    // Check expiration
    if (Date.now() > authCode.expiresAt) {
        authorizationCodes.delete(code);
        res.status(400).json({ error: 'invalid_grant', error_description: 'Code expired' });
        return;
    }
    
    // Verify PKCE if code challenge was provided
    if (authCode.codeChallenge) {
        if (!code_verifier) {
            res.status(400).json({ error: 'invalid_request', error_description: 'code_verifier required' });
            return;
        }
        const valid = await verifyCodeChallenge(code_verifier, authCode.codeChallenge, authCode.codeChallengeMethod || 'plain');
        if (!valid) {
            res.status(400).json({ error: 'invalid_grant', error_description: 'Invalid code_verifier' });
            return;
        }
    }
    
    // Delete used authorization code
    authorizationCodes.delete(code);
    
    // Generate access token
    const accessToken = randomUUID();
    const tokenData: AccessToken = {
        token: accessToken,
        clientId: authCode.clientId,
        scopes: authCode.scopes,
        magentoBaseUrl: authCode.magentoBaseUrl,
        magentoToken: authCode.magentoToken,
        expiresAt: Date.now() + 24 * 60 * 60 * 1000 // 24 hours
    };
    
    accessTokens.set(accessToken, tokenData);
    log('info', 'Access token issued', { clientId: authCode.clientId });
    
    res.json({
        access_token: accessToken,
        token_type: 'Bearer',
        expires_in: 86400,
        scope: authCode.scopes.join(' ')
    });
});

// ============================================================================
// Token Verification Helper
// ============================================================================

function verifyAccessToken(authHeader: string | undefined): AccessToken | null {
    if (!authHeader) return null;
    
    const parts = authHeader.split(' ');
    if (parts.length !== 2 || parts[0].toLowerCase() !== 'bearer') return null;
    
    const token = parts[1];
    const tokenData = accessTokens.get(token);
    
    if (!tokenData) return null;
    if (Date.now() > tokenData.expiresAt) {
        accessTokens.delete(token);
        return null;
    }
    
    return tokenData;
}

// ============================================================================
// MCP Endpoint with Streamable HTTP Transport
// ============================================================================

const transports: Record<string, StreamableHTTPServerTransport> = {};

app.post('/mcp', async (req: Request, res: Response) => {
    const sessionId = req.headers['mcp-session-id'] as string | undefined;
    
    log('debug', 'POST /mcp', { sessionId, hasAuth: !!req.headers.authorization, isInit: isInitializeRequest(req.body) });
    
    // Reuse existing session
    if (sessionId && transports[sessionId]) {
        log('debug', 'Reusing session', { sessionId });
        await transports[sessionId].handleRequest(req, res, req.body);
        return;
    }
    
    // New session requires initialization
    if (!isInitializeRequest(req.body)) {
        res.status(400).json({
            jsonrpc: '2.0',
            error: { code: -32000, message: 'Bad Request: No valid session found' },
            id: req.body?.id ?? null
        });
        return;
    }
    
    // Verify access token
    const tokenData = verifyAccessToken(req.headers.authorization as string);
    if (!tokenData) {
        log('warn', 'Unauthorized MCP request');
        res.status(401).json({
            jsonrpc: '2.0',
            error: { code: -32001, message: 'Unauthorized' },
            id: (req.body as any)?.id ?? null
        });
        return;
    }
    
    log('info', 'Creating new MCP session', { baseUrl: tokenData.magentoBaseUrl });
    
    try {
        const { schema, environment } = await getMagentoContext(tokenData.magentoBaseUrl, tokenData.magentoToken);
        const axiosInstance = createAxiosInstance(tokenData.magentoBaseUrl, tokenData.magentoToken);
        
        const server = new McpServer({
            name: `${MCP_TITLE} (${tokenData.magentoBaseUrl})`,
            version: "2.2.0"
        });
        
        registerTools(server, axiosInstance, schema, environment);
        
        const transport = new StreamableHTTPServerTransport({
            sessionIdGenerator: () => randomUUID(),
            onsessioninitialized: (sid) => {
                log('info', 'Session initialized', { sessionId: sid });
                transports[sid] = transport;
            }
        });
        
        transport.onclose = () => {
            const sid = transport.sessionId;
            if (sid && transports[sid]) {
                log('info', 'Session closed', { sessionId: sid });
                delete transports[sid];
            }
        };
        
        await server.connect(transport);
        await transport.handleRequest(req, res, req.body);
        
    } catch (error: any) {
        log('error', 'Session creation failed', { error: error.message });
        res.status(500).json({
            jsonrpc: '2.0',
            error: { code: -32603, message: error.message },
            id: (req.body as any)?.id ?? null
        });
    }
});

app.get('/mcp', async (req: Request, res: Response) => {
    const sessionId = req.headers['mcp-session-id'] as string;
    if (!sessionId || !transports[sessionId]) {
        res.status(400).json({ jsonrpc: '2.0', error: { code: -32000, message: 'Invalid session' }, id: null });
        return;
    }
    await transports[sessionId].handleRequest(req, res);
});

app.delete('/mcp', async (req: Request, res: Response) => {
    const sessionId = req.headers['mcp-session-id'] as string;
    if (!sessionId || !transports[sessionId]) {
        res.status(400).json({ jsonrpc: '2.0', error: { code: -32000, message: 'Invalid session' }, id: null });
        return;
    }
    await transports[sessionId].handleRequest(req, res);
});

// ============================================================================
// Health & Info Endpoints
// ============================================================================

app.get('/health', (_req, res) => {res.json({ status: 'ok' });});

app.get('/', (_req, res) => {
    res.json({
        name: MCP_TITLE,
        version: '2.2.0',
        mcp_endpoint: '/mcp',
        oauth: {
            authorization_endpoint: '/authorize',
            token_endpoint: '/token',
            registration_endpoint: '/register',
            metadata: '/.well-known/oauth-authorization-server'
        }
    });
});

// ============================================================================
// Start Server
// ============================================================================

app.listen(PORT, '0.0.0.0', () => {
    console.log(`
╔═══════════════════════════════════════════════════════════════════╗
║                  ${MCP_TITLE} MCP Server v2.2.0                    
╠═══════════════════════════════════════════════════════════════════╣
║                                                                   
║  MCP Endpoint:     ${BASE_URL}/mcp                     
║  OAuth Metadata:   ${BASE_URL}/.well-known/oauth-authorization-server
║                                                                   
║  For MCP Inspector:                                               
║    URL: ${BASE_URL}/mcp                                
║    (OAuth flow will start automatically)                          
║                                                                   
║  Ready to accept connections!                                     
╚═══════════════════════════════════════════════════════════════════╝
`);
});
