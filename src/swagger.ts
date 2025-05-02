import { AxiosInstance } from 'axios';

// Define a more accurate structure based on OpenAPI/Swagger 2.0 schema
export interface OpenApiPathItem {
    get?: OpenApiOperation;
    put?: OpenApiOperation;
    post?: OpenApiOperation;
    delete?: OpenApiOperation;
    options?: OpenApiOperation;
    head?: OpenApiOperation;
    patch?: OpenApiOperation;
    parameters?: any[]; // Define more specific type if needed
}

export interface OpenApiOperation {
    tags?: string[];
    summary?: string;
    description?: string;
    operationId?: string;
    consumes?: string[];
    produces?: string[];
    parameters?: any[]; // Define more specific type if needed
    responses: { [statusCode: string]: OpenApiResponse };
    security?: any[]; // Define more specific type if needed
}

export interface OpenApiResponse {
    description: string;
    schema?: any; // Define more specific type if needed
    headers?: { [headerName: string]: any }; // Define more specific type if needed
    examples?: { [mimeType: string]: any };
}

export interface MagentoApiSchema {
    swagger: string;
    info: {
        version: string;
        title: string;
    };
    host: string;
    basePath: string;
    schemes: string[];
    tags?: { name: string; description?: string }[];
    paths: {
        [path: string]: OpenApiPathItem;
    };
    definitions?: { [definitionName: string]: any }; // Define more specific type if needed
    // Add other top-level Swagger properties if needed (securityDefinitions, tags, etc.)
}

export interface MagentoEnvironment {
    websites: any[]; // Define more specific type if needed
    storeGroups: any[]; // Define more specific type if needed
    storeViews: any[]; // Define more specific type if needed
}

export async function fetchMagentoApiSchema(axiosInstance: AxiosInstance): Promise<MagentoApiSchema> {
    const schema = (await axiosInstance.get<MagentoApiSchema>(
        '/all/schema?services=all'
    ))?.data;
    
    if (!schema) {
        throw new Error('Failed to fetch Magento API schema');
    }

    return {
        ...schema,
        tags: [],
    }
}

export async function fetchMagentoApiEnvironment(axiosInstance: AxiosInstance): Promise<MagentoEnvironment> {
    const websites = (await axiosInstance.get('/all/V1/store/websites')).data;
    const storeGroups = (await axiosInstance.get('/all/V1/store/storeGroups')).data.filter((storeGroup: any) => storeGroup.id > 0);
    const storeViews = (await axiosInstance.get('/all/V1/store/storeViews')).data;

    return {
        websites,
        storeGroups,
        storeViews,
    };
}
