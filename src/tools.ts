import { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import axios, { AxiosInstance } from "axios";

// Define the type for parameters expected by callMagentoApi
export interface CallApiParams {
    method: 'get' | 'post' | 'put' | 'delete';
    path: string;
    query?: string | null;
    body?: Record<string, any> | null; // Changed body type from string | null
}

function parseQuerystring(s: string): Record<string, string> | null { // Added null return type possibility
    if (!s || s.length === 0) {
        return null;
    }
    let query: Record<string, string> = {};
    const pairs = (s[0] === '?' ? s.substr(1) : s).split('&');
    for (const pairStr of pairs) {
        const pair = pairStr.split('=');
        if (pair.length > 0 && pair[0]) { // Ensure there's a key
            query[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1] || '');
        }
    }
    // Return null if the query object is empty after parsing
    return Object.keys(query).length > 0 ? query : null;
}

export async function callMagentoApi(axiosInstance: AxiosInstance, request: CallApiParams): Promise<CallToolResult> {
    const queryParams = request.query?.length > 0 ? parseQuerystring(request.query) : null;
    if (queryParams) {
        for (const [key, value] of Object.entries(queryParams)) {
            request.path = request.path.replace(`{${key}}`, value);
        }
    }

    let responseText = '';
    try {
        console.warn("Calling Magento 2 API", request.method, request.path, queryParams, request.body);
        const response = await axiosInstance.request({
            method: request.method,
            url: request.path,
            params: queryParams ?? undefined, // Pass undefined if queryParams is null
            data: request.body ?? undefined, // Pass request.body directly (object or null/undefined)
        });
        // Ensure response data is stringified if it's an object/array
        responseText = typeof response.data === 'string' ? response.data : JSON.stringify(response.data);
    } catch (error) {
        if (axios.isAxiosError(error)) {
            if (error.response) {
                responseText = JSON.stringify(error.response.data);
            } else {
                responseText = error.message;
            }
            throw new Error(`Error calling Magento API: ${responseText}`);
        }
    }

    return {
        content: [
            {
                type: "text",
                text: typeof responseText !== 'string' ? JSON.stringify(responseText) : responseText,
            },
        ],
    };
}
