import axios from 'axios';
import https from 'https'; // Import https module
import express from 'express';
import cors from 'cors';
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
import { z } from "zod";
import { fetchMagentoApiEnvironment, fetchMagentoApiSchema, MagentoApiSchema } from './swagger.js'; // Added .js extension
import { callMagentoApi, CallApiParams } from './tools.js'; // Added .js extension and CallApiParams import
import dotenv from 'dotenv';

dotenv.config();

let featuredApis: {[k: string]: {description: string, target: string}} = {};
for (const [key, value] of Object.entries(process.env)) {
    if (value && value?.length > 0 && key.startsWith('FEATURED_APIS_') && !key.includes("DESCRIPTION")) {
        const parts = value.split('::');
        if (parts.length === 3) {
            const desciption = process.env[`${key}_DESCRIPTION`];
            featuredApis[parts[0]] = {
                description: desciption ?? parts[0],
                target: `${parts[1]}::${parts[2]}`
            };
        } else {
            console.error(`Invalid format for ${key}: ${value}`);
        }
    }
}

const MCP_TITLE = process.env.MCP_TITLE || '';
const MAGENTO_BASE_URL = process.env.MAGENTO_BASE_URL;
const MAGENTO_INTEGRATION_TOKEN = process.env.MAGENTO_INTEGRATION_TOKEN;
const FEATURED_APIS = featuredApis;

const axiosInstance = axios.create({
    baseURL: MAGENTO_BASE_URL + '/rest',
    headers: {
        Authorization: `Bearer ${MAGENTO_INTEGRATION_TOKEN}`,
        'Content-Type': 'application/json',
    },
    httpsAgent: new https.Agent({
        rejectUnauthorized: false,
    }),
});

let schema = await fetchMagentoApiSchema(axiosInstance);
let magento2Environment = await fetchMagentoApiEnvironment(axiosInstance);

setInterval(async () => {
    try {
        schema = await fetchMagentoApiSchema(axiosInstance);
        magento2Environment = await fetchMagentoApiEnvironment(axiosInstance);
    } catch (error) {
        console.error("Error fetching Magento API schema or environment:", error);
    }
}, 1 * 60 * 60); // 1 hour

const server = new McpServer({
    name: (MCP_TITLE + " Magento 2").trim(),
    version: "2.2.0"
});

for (const [name, api] of Object.entries(FEATURED_APIS)) {
    const { description, target } = api;
    const [method, path] = target.split('::');

    if (!schema.paths[path]) {
        console.error(`Path ${path} not found in schema. Skpping featured API tool export`);
        continue;
    }

    if (!schema.paths[path][method]) {
        console.error(`Method ${method} not found for path ${path} in schema. Skpping featured API tool export`);
        continue;
    }

    let toolSignature: any = {
        storeCode: z.nullable(z.string()).default("all").describe('Store code (e.g. default'),
    };

    for (const params of schema.paths[path][method].parameters ?? []) {
        if (params.in === 'path') {
            let toolParamSignature: any = params.type === 'integer' ? z.number() : z.string();
            toolParamSignature = params.required ? toolParamSignature : z.nullable(toolParamSignature);
            toolSignature = {
                ...toolSignature,
                [params.name]: toolParamSignature,
            };
        } else if (params.in === 'query') {
            let toolParamSignature: any = z.string();
            toolParamSignature = params.required ? toolParamSignature : z.nullable(toolParamSignature).optional();
            toolSignature = {
                ...toolSignature,
                [params.name]: toolParamSignature,
            };
        }
        else if (params.in === 'body') {
            toolSignature = Object.entries(params.schema.properties).reduce((acc, [key, value]) => {
                let toolParamSignature: any = (value as any)?.type === 'integer' ? z.number() : z.string();
                toolParamSignature = params.schema.required?.includes(key) ? toolParamSignature : z.nullable(toolParamSignature).optional();
                acc[key] = toolParamSignature;
                return acc;
            }, toolSignature ?? {} as Record<string, z.ZodTypeAny>);
        }
    }

    server.tool(
        name,
        description,
        toolSignature,
        async (params: Record<string, any>) => {
            console.warn("Got managed tool call:", name, params);
            const callParams: CallApiParams = {
                method: method.toLowerCase() as CallApiParams['method'],
                storeCode: params.storeCode ?? null,
                path: path,
                query: null,
                body: null,
            };
            params.storeCode = undefined;

            if (method.toLowerCase() === 'get' || method.toLowerCase() === 'delete') {
                // For GET/DELETE, params are query params
                const queryString = new URLSearchParams(params).toString();
                callParams.query = queryString.length > 0 ? `?${queryString}` : null;
            } else {
                const pathParamNames = (path.match(/{([^}]+)}/g) || []).map(p => p.replace(/{|}/g, ''));

                const pathParams: Record<string, any> = {};
                const otherParams: Record<string, any> = {};
                for (const key in params) {
                    if (pathParamNames.includes(key)) {
                        pathParams[key] = params[key];
                    } else {
                        otherParams[key] = params[key];
                    }
                }

                const queryString = new URLSearchParams(pathParams).toString();
                callParams.query = queryString.length > 0 ? `?${queryString}` : null;
                callParams.body = otherParams;
            }

            return await callMagentoApi(axiosInstance, callParams);
        }
    );
}

server.tool(
    "lvl0_info__get_deployment_info",
    "Get deployment information (urls, versions, store codes, currencies, etc.)",
    {},
    async () => {
        const environment = {
            php: {
                executable: process.env.MAGENTO_PHP_EXECUTABLE,
                version: process.env.MAGENTO_PHP_VERSION,
            },
            magento: {
                frontendBaseUrl: MAGENTO_BASE_URL,
                fsRoot: process.env.MAGENTO_FS_ROOT,
                version: process.env.MAGENTO_VERSION,
                edition: process.env.MAGENTO_EDITION,
                mode: process.env.MAGENTO_MODE,
            }
        };

        return {
            content: [{
                type: 'text',
                text: JSON.stringify({
                    environment,
                    websites: magento2Environment.websites,
                    stores: {
                        storeGroups: magento2Environment.storeGroups,
                        storeViews: magento2Environment.storeViews,
                    },
                }),
            }]
        };
    }
);

server.tool(
    "lvl1_rest__get_api_definitions",
    "Get OpenAPI schema definitions",
    {},
    () => {
        // Explicitly copy properties instead of spreading potentially non-object schema
        const definitions: Partial<MagentoApiSchema> = {
            swagger: schema.swagger,
            info: schema.info,
            host: schema.host,
            basePath: schema.basePath,
            schemes: schema.schemes,
            definitions: schema.definitions,
            // tags: schema.tags, // Exclude tags as it might be optional or handled differently
            paths: {}, // Keep paths empty as intended
        };
        return {
            content: [{
                type: 'text',
                text: JSON.stringify(definitions),
            }]
        };
    }
);

// Helper function for recursive definition collection
function collectAllReferencedDefinitions(
    definitionName: string,
    allSchemaDefinitions: typeof schema.definitions, // Assuming schema is accessible globally or passed correctly
    collectedDefinitions: Map<string, any>,
    currentDepth: number,
    maxDepth: number
) {
    if (currentDepth >= maxDepth || collectedDefinitions.has(definitionName)) {
        return;
    }

    const definitionObject = allSchemaDefinitions[definitionName];
    if (!definitionObject) {
        // console.warn(`Recursive search: Definition ${definitionName} not found in schema.`);
        return; // Definition not found, stop recursion for this path
    }

    collectedDefinitions.set(definitionName, definitionObject);

    // Search for further references within this definition object
    const definitionString = JSON.stringify(definitionObject);
    const regex = /"#\/definitions\/([^"]*)"/g; // Regex to find references like "#/definitions/SomeType"
    let matches;

    while ((matches = regex.exec(definitionString)) !== null) {
        const referencedDefName = matches[1]; // matches[1] is the captured group (e.g., "SomeType")
        if (referencedDefName) {
            collectAllReferencedDefinitions(
                referencedDefName,
                allSchemaDefinitions,
                collectedDefinitions,
                currentDepth + 1,
                maxDepth
            );
        }
    }
}

server.tool(
    "lvl1_rest__search_api_methods",
    "Search OpenAPI schema for API methods by keyword(s) (e.g. products, orders, etc.) and include all referenced definitions recursively (max depth 10).",
    { 
        search: z.nullable(z.string()).describe('Search keywords (e.g. products, orders, etc.)'),
    },
    ({ search }) => {
        console.warn("Search API methods called with keyword:", search); // Changed console.error to console.warn for consistency
        let paths = schema.paths;

        if (search) {
            const searchTerms = search.toLowerCase().split(' ').filter(term => term.length > 0);
            paths = Object.fromEntries(
                Object.entries(paths).filter(([pathKey]) => 
                    searchTerms.some((term) => pathKey.toLowerCase().includes(term))
                )
            );
        }

        const resultPaths = Object.entries(paths).map(([path, item]) => {
            return {
                type: 'text',
                text: JSON.stringify({path, ...item}),
            };
        });

        const definitionRefRegex = /"#\/definitions\/([^"]*)"/g;
        const knownDefinitionsMasterSet = new Set(Object.keys(schema.definitions));
        const initialReferencedDefinitionNames = new Set<string>();

        // 1. Collect initial definitions referenced directly in the API paths
        for (const pathData of resultPaths) {
            let matches;
            // Reset lastIndex before each new execution on a new string
            definitionRefRegex.lastIndex = 0; 
            while ((matches = definitionRefRegex.exec(pathData.text)) !== null) {
                const defName = matches[1]; // The captured definition name
                if (defName && knownDefinitionsMasterSet.has(defName)) {
                    initialReferencedDefinitionNames.add(defName);
                }
            }
        }

        // 2. Recursively collect all definitions
        const allCollectedDefinitions = new Map<string, any>();
        const MAX_DEPTH = 10;

        for (const defName of initialReferencedDefinitionNames) {
            collectAllReferencedDefinitions(
                defName,
                schema.definitions, // Pass the global schema.definitions
                allCollectedDefinitions,
                0, // Start at depth 0
                MAX_DEPTH
            );
        }
        
        // Convert Map to object for JSON stringification
        const finalResultDefinitionsObject = Object.fromEntries(allCollectedDefinitions);

        return {
            content: resultPaths.concat([{
                type: 'text',
                text: JSON.stringify({definitions: finalResultDefinitionsObject}),
            }]) as any,
        };
    }
);

server.tool(
    "lvl1_rest__call_api_method",
    "Call any known REST API method",
    {
        method: z.enum(['GET', 'get', 'POST', 'post', 'PUT', 'put', 'DELETE', 'delete']),
        storeCode: z.nullable(z.string()).default("all").describe('Store code (e.g. default'),
        path: z.string().describe('Path to the API method (e.g. /V1/products)'),
        query: z.nullable(z.string()).optional().describe('Nullable query parameters as querystring (e.g. ?param1=value1&param2=value2)'),
        body: z.nullable(z.record(z.any())).optional().describe('Nullable request body as a JSON object'),
    },
    // Adjust the handler to match the new CallApiParams and Zod schema
    async (params: {
        method: 'GET' | 'get' | 'POST' | 'post' | 'PUT' | 'put' | 'DELETE' | 'delete';
        storeCode: string | null;
        path: string;
        query: string | null;
        body: Record<string, any> | null; // Match the Zod schema type
    }) => await callMagentoApi(axiosInstance, {
        ...params,
        method: params.method.toLowerCase() as CallApiParams['method'], // Ensure method is lowercase
    }),
);

if (process.env.SSE === 'true') {
    console.log('Starting server in SSE mode');
    const app = express();
    const PORT = process.env.SSE_PORT || 3000;
    
    // Enable CORS
    app.use(cors());
    app.use(express.json());
    
    // to support multiple simultaneous connections
    const transports: {[sessionId: string]: SSEServerTransport} = {};
    
    // POST endpoint for clients to send messages
    app.post('/messages', async (req, res) => {
        console.log("Message request received");
        const sessionId = req.query.sessionId;
        
        if (typeof sessionId !== 'string') {
            res.status(400).send({ message: "Bad session id" });
            return;
        }
        
        const transport = transports[sessionId];
        if (!transport) {
            res.status(400).send({ message: "No transport found for sessionId" });
            return;
        }
        
        await transport.handlePostMessage(req, res, req.body);
    });
    
    // SSE connection endpoint
    app.get('/connect', async (req, res) => {
        console.log('Client connecting to SSE');
        
        // Create new transport for this connection
        const transport = new SSEServerTransport('/messages', res);
        console.log(`New transport created with session id: ${transport.sessionId}`);
        
        transports[transport.sessionId] = transport;
        
        // Clean up on connection close
        res.on('close', () => {
            console.log(`SSE connection closed for session ${transport.sessionId}`);
            delete transports[transport.sessionId];
        });
        
        // Connect the transport to our MCP server
        await server.connect(transport);
        
        // Send welcome message
        await transport.send({
            jsonrpc: "2.0",
            method: "sse/connection",
            params: { message: "Magento MCP server connected" }
        });
    });
    
    app.listen(PORT, () => {
        console.log(`Magento MCP SSE server listening on port ${PORT}`);
    });
} else {
    // Default stdio transport when SSE is not enabled
    const stdioTransport = new StdioServerTransport();
    await server.connect(stdioTransport);
    console.warn("Connected with stdio transport");
}
