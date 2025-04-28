import { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import axios, { AxiosInstance } from "axios";

// Define the type for parameters expected by callMagentoApi
export interface CallApiParams {
    method: 'get' | 'post' | 'put' | 'delete';
    path: string;
    query: string | null;
    body: object | null;
}

export async function callMagentoApi(axiosInstance: AxiosInstance, request: CallApiParams): Promise<CallToolResult> {
    // replace path params in path with values from query
    if (request.query) {
        for (const [key, value] of Object.entries(request.query)) {
            request.path = request.path.replace(`{${key}}`, value);
        }
    }

    let responseText = '';
    try {
        const response = await axiosInstance.request({
            method: request.method,
            url: request.path,
            params: request.query,
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
        } else {
            responseText = (error as Error).message;
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
