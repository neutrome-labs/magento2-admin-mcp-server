# Use an official Node.js runtime as a parent image
FROM node:20-slim

# Install git and pnpm
RUN apt-get update && apt-get install -y git && npm install -g pnpm

# Set the working directory
WORKDIR /app

# Clone the repository
# Using . to clone into the current directory (/app)
RUN git clone https://github.com/cgoinglove/mcp-client-chatbot .

# Install dependencies
RUN pnpm install

COPY cgoinglove-mcp-client-chatbot/mcp-config.json .mcp-config.json

# Copy package files and install Node.js dependencies (including typescript devDependency)
COPY package*.json ./magento2-admin-mcp-server-v2/
RUN cd ./magento2-admin-mcp-server-v2/ && npm install

# Copy tsconfig.json
COPY tsconfig.json ./magento2-admin-mcp-server-v2/

# Copy the application source code from the src directory
COPY src ./magento2-admin-mcp-server-v2/src

# Compile TypeScript to JavaScript using the local install
RUN cd ./magento2-admin-mcp-server-v2/ && npx tsc --project tsconfig.json

# Expose a default port (adjust if needed)
EXPOSE 3000

# Run the development server
CMD ["sh", "-c", "pnpm run initial && pnpm run dev"]