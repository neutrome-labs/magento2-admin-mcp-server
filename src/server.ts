import axios from 'axios';
import https from 'https'; // Import https module
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { fetchMagentoApiSchema, MagentoApiSchema } from './swagger.js'; // Added .js extension
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

const schema = await fetchMagentoApiSchema(axiosInstance);

const server = new McpServer({
    name: (MCP_TITLE + " Magento 2").trim(),
    version: "2.1.0"
});

for (const [name, api] of Object.entries(FEATURED_APIS)) {
    const { description, target } = api;
    const [method, path] = target.split('::');

    let toolSignature: any = {};

    const pathParams = path.match(/{([^}]+)}/g);
    if (pathParams) {
        for (const param of pathParams) {
            const paramName = param.replace(/{|}/g, '');
            toolSignature[paramName] = z.string();
        }
    }

    if (!schema.paths[path]) {
        console.error(`Path ${path} not found in schema. Skpping featured API tool export`);
        continue;
    }

    if (!schema.paths[path][method]) {
        console.error(`Method ${method} not found for path ${path} in schema. Skpping featured API tool export`);
        continue;
    }

    for (const params of schema.paths[path][method].parameters ?? []) {
        if (params.in === 'query') {
            let toolParamSignature: any = null;
            if (params.required) {
                toolParamSignature = z.string();
            } else {
                toolParamSignature = z.nullable(z.string());
            }
            toolSignature = {
                ...toolSignature,
                [params.name]: toolParamSignature,
            };
        }
        else if (params.in === 'body') {
            toolSignature = Object.entries(params.schema.properties).reduce((acc, [key, value]) => {
                acc[key] = params.schema.required!.includes(key) ? z.string() : z.nullable(z.string());
                return acc;
            }, toolSignature ?? {} as Record<string, z.ZodTypeAny>);
        }
    }

    server.tool(
        name,
        description,
        toolSignature,
        async (params: CallApiParams) => await callMagentoApi(axiosInstance, {
            method: method as CallApiParams['method'], // Assert type here
            path,
            query: method !== 'post' ? JSON.parse(JSON.stringify(params)) : null,
            body: method === 'post' ? JSON.stringify(params) : null,
        })
    );
}

server.tool(
    "lvl1_rest__get_api_definitions",
    "Allows to get OpenAPI schema definitions",
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
    "Allows to search OpenAPI schema for API methods by keyword(s) (e.g. products, orders, etc.)",
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
    "Allows to call any known REST API method",
    {
        method: z.enum(['GET', 'get', 'POST', 'post', 'PUT', 'put', 'DELETE', 'delete']),
        path: z.string(),
        query: z.nullable(z.string()).describe('Nullable query parameters as querystring (e.g. ?param1=value1&param2=value2)'),
        body: z.nullable(z.string()).describe('Nullable request body as JSON string'),
    },
    // Explicitly type params to match the expected structure for callMagentoApi
    async (params: CallApiParams) => await callMagentoApi(axiosInstance, {
        ...params,
        method: params.method.toLowerCase() as CallApiParams['method'], // Assert type here
    }),
);

// Start receiving messages on stdin and sending messages on stdout
const transport = new StdioServerTransport();
await server.connect(transport);
