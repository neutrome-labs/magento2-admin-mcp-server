import { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import axios, { AxiosInstance } from "axios";

// Define the type for parameters expected by callMagentoApi
export interface CallApiParams {
    method: 'get' | 'post' | 'put' | 'delete';
    path: string;
    query: string | null;
    body: string | null;
}

function parseQuerystring(s: string): Record<string, string> {
    let nonNull = false;
    let query = {};
    var pairs = (s[0] === '?' ? s.substr(1) : s).split('&');
    for (var i = 0; i < pairs.length; i++) {
        var pair = pairs[i].split('=');
        query[decodeURIComponent(pair[0])] = decodeURIComponent(pair[1] || '');
        nonNull = true;
    }
    return nonNull ? query : null;
}

export async function callMagentoApi(axiosInstance: AxiosInstance, request: CallApiParams): Promise<CallToolResult> {
    const queryParams = parseQuerystring(request.query);
    if (queryParams) {
        for (const [key, value] of Object.entries(queryParams)) {
            request.path = request.path.replace(`{${key}}`, value);
        }
    }

    let responseText = '';
    try {
        const response = await axiosInstance.request({
            method: request.method,
            url: request.path,
            params: queryParams,
            data: request.body ? request.body : undefined,
        });
        responseText = response.data;
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
