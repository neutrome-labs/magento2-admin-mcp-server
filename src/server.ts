import axios from 'axios';
import https from 'https'; // Import https module
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
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

server.tool(
    "lvl1_rest__search_api_methods",
    "Search OpenAPI schema for API methods by keyword(s) (e.g. products, orders, etc.)",
    { 
        search: z.nullable(z.string()).describe('Search keywords (e.g. products, orders, etc.)'),
    },
    ({ search }) => {
        console.error("search", search);
        let paths = schema.paths;

        if (search) {
            const searchs = search.split(' ');
            paths = Object.fromEntries(
                Object.entries(paths).filter(([path]) => searchs.some((search) => path.toLowerCase().includes(search.toLowerCase())))
            );
        }

        return {
            content: Object.entries(paths).map(([path, item]) => {
                return {
                    type: 'text',
                    text: JSON.stringify({path, ...item}),
                };
            })
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

// Start receiving messages on stdin and sending messages on stdout
const transport = new StdioServerTransport();
await server.connect(transport);
