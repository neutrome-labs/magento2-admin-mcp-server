import { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { AxiosInstance } from "axios";

export async function callMagentoApi(axiosInstance: AxiosInstance, request: {
    method: string,
    path: string,
    queryParams: Record<string, string> | null,
    body: string | null
}): Promise<CallToolResult> {

    // replace path params in path with values from queryParams
    if (request.queryParams) {
        for (const [key, value] of Object.entries(request.queryParams)) {
            request.path = request.path.replace(`{${key}}`, value);
        }
    }

    const response = await axiosInstance.request({
        method: request.method,
        url: request.path,
        params: request.queryParams,
        data: request.body ? JSON.parse(request.body) : undefined,
    });

    return {
        content: [
            {
                type: "text",
                text: JSON.stringify(response.data, null, 2),
            },
        ],
    };
}