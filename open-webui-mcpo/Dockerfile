# Use the official mcpo base image which includes mcpo and Node.js
FROM ghcr.io/open-webui/mcpo:latest

# Set the working directory
WORKDIR /app

# Copy package files and install Node.js dependencies (including typescript devDependency)
COPY package*.json ./
RUN npm install

# Copy tsconfig.json
COPY tsconfig.json ./

# Copy the application source code from the src directory
COPY src ./src

# Compile TypeScript to JavaScript using the local install
RUN npx tsc --project tsconfig.json

# Copy the configuration file
COPY open-webui-mcpo/config.json ./

# Expose the port mcpo will run on (default is 8000, adjust if needed)
EXPOSE 8000

# Define the command to run mcpo (from base image) proxying to the compiled Node.js MCP server
CMD ["mcpo", "--host", "0.0.0.0", "--port", "8000", "--config", "config.json"]