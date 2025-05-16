"""
Work item attachment operations for Azure DevOps.

This module provides MCP tools for downloading, uploading and managing work item attachments.
"""
from typing import Dict, Any, Optional, BinaryIO
import os
import sys
import mimetypes
import io
import logging

from azure.devops.v7_1.work_item_tracking import WorkItemTrackingClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='/tmp/mcp_azure_devops.log'
)

from mcp_azure_devops.features.work_items.common import (
    AzureDevOpsClientError,
    get_work_item_client,
)


def _get_attachment_info_from_work_item(
    work_item_id: int,
    attachment_id: str,
    wit_client: WorkItemTrackingClient
) -> Dict[str, Any]:
    """
    Get attachment information from a work item.
    
    Args:
        work_item_id: The work item ID
        attachment_id: The attachment ID
        wit_client: Work item tracking client
    
    Returns:
        Dictionary with attachment information
    """
    try:
        # Get work item with its relations
        work_item = wit_client.get_work_item(work_item_id, expand="relations")
        
        if not work_item or not work_item.relations:
            raise AzureDevOpsClientError(
                f"No attachments found in work item {work_item_id}")
        
        # Find the relation that corresponds to the attachment
        for relation in work_item.relations:
            if (relation.rel == "AttachedFile" and 
                    attachment_id in relation.url):
                # Extract filename from URL
                filename = os.path.basename(relation.attributes.get("name", ""))
                return {
                    "name": filename,
                    "url": relation.url,
                    "attributes": relation.attributes
                }
        
        raise AzureDevOpsClientError(
            f"Attachment {attachment_id} not found in work item {work_item_id}")
            
    except Exception as e:
        raise AzureDevOpsClientError(
            f"Error getting attachment information: {str(e)}")


def _download_attachment_impl(
    work_item_id: int,
    attachment_id: str,
    output_path: str,
    wit_client: WorkItemTrackingClient
) -> Dict[str, Any]:
    """
    Implementation of attachment download.
    
    Args:
        work_item_id: The work item ID
        attachment_id: The attachment ID
        output_path: Path where to save the downloaded file
        wit_client: Work item tracking client
    
    Returns:
        Information about the downloaded file
    """
    try:
        # Get attachment information from work item
        attachment_info = _get_attachment_info_from_work_item(
            work_item_id, attachment_id, wit_client)
        
        # Get attachment content generator
        attachment_content_generator = wit_client.get_attachment_content(attachment_id)
        
        downloads_dir = "/downloads"
        # Handle both Windows and Linux paths
        if "\\" in output_path or (":" in output_path and len(output_path.split(":")[0]) == 1):
            # It's a Windows path, extract filename
            filename = output_path.replace("\\", "/").split("/")[-1]
        else:
            # It's a Linux path or just a filename
            filename = os.path.basename(output_path)
        
        final_path = os.path.join(downloads_dir, filename)

        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(final_path)), exist_ok=True)
        
        # Save content to file iterating over the generator
        total_size = 0
        with open(final_path, 'wb') as f:
            # Iterate directly over the generator
            for chunk in attachment_content_generator:
                f.write(chunk)
                total_size += len(chunk)
        
        return {
            "filename": attachment_info["name"],
            "size": total_size,
            "content_type": attachment_info["attributes"].get("contentType", "application/octet-stream"),
            "saved_to": final_path
        }
    except Exception as e:
        raise AzureDevOpsClientError(
            f"Error downloading attachment {attachment_id} from work item "
            f"{work_item_id}: {str(e)}")


def _upload_attachment_impl(
    file_path: str,
    file_name: Optional[str],
    comment: Optional[str],
    wit_client: WorkItemTrackingClient
) -> Dict[str, Any]:
    """
    Implementation of attachment upload.
    
    Args:
        file_path: Path to the file to upload
        file_name: Optional name to use for the file in Azure DevOps
        comment: Optional comment about the attachment
        wit_client: Work item tracking client
    
    Returns:
        Information about the uploaded attachment
    """
    try:
        # Handle path translation from host to container
        container_path = file_path
        
        # Check if this looks like a host path (Windows or Linux)
        if "\\" in file_path or (":" in file_path and len(file_path.split(":")[0]) == 1):
            # This is likely a Windows path, extract filename
            filename = file_path.replace("\\", "/").split("/")[-1]
            # Try in common container directories
            possible_locations = [
                os.path.join("/downloads", filename),
                os.path.join("/uploads", filename),
                os.path.join("/tmp", filename)
            ]
            
            # Check each possible location
            container_path = None
            for loc in possible_locations:
                if os.path.exists(loc):
                    container_path = loc
                    break
            
            if not container_path:
                raise AzureDevOpsClientError(
                    f"File not found: {file_path}. Please ensure the file is available "
                    f"in the container in one of these directories: /downloads, /uploads, or /tmp."
                )
        elif file_path.startswith("/"):
            # This is likely already a container path (Linux style)
            if not os.path.exists(file_path):
                # Try common prefixes if the exact path doesn't exist
                if not file_path.startswith(("/downloads/", "/uploads/", "/tmp/")):
                    filename = os.path.basename(file_path)
                    possible_locations = [
                        os.path.join("/downloads", filename),
                        os.path.join("/uploads", filename),
                        os.path.join("/tmp", filename)
                    ]
                    
                    # Check each possible location
                    container_path = None
                    for loc in possible_locations:
                        if os.path.exists(loc):
                            container_path = loc
                            break
                    
                    if not container_path:
                        raise AzureDevOpsClientError(
                            f"File not found: {file_path}. Please ensure the file is available "
                            f"in the container in one of these directories: /downloads, /uploads, or /tmp."
                        )
                else:
                    # Path already has the correct prefix but file doesn't exist
                    raise AzureDevOpsClientError(f"File not found: {file_path}")
        else:
            # This is a relative path, try to find it in common locations
            filename = os.path.basename(file_path)
            possible_locations = [
                os.path.join("/downloads", file_path),
                os.path.join("/uploads", file_path),
                os.path.join("/tmp", file_path),
                file_path  # Try as-is too
            ]
            
            # Check each possible location
            container_path = None
            for loc in possible_locations:
                if os.path.exists(loc):
                    container_path = loc
                    break
            
            if not container_path:
                raise AzureDevOpsClientError(
                    f"File not found: {file_path}. Please ensure the file is available "
                    f"in the container in one of these directories: /downloads, /uploads, or /tmp."
                )
        
        # If file_name is not provided, use the original file name
        if not file_name:
            file_name = os.path.basename(file_path)
        
        # Guess content type based on file extension
        content_type, _ = mimetypes.guess_type(file_name)
        if not content_type:
            content_type = "application/octet-stream"
        
        # Upload the attachment to Azure DevOps
        # Note: The API expects a file-like object with a read method,
        # not just the bytes content
        try:
            with open(container_path, 'rb') as f:
                attachment = wit_client.create_attachment(
                    upload_stream=f,
                    file_name=file_name,
                    upload_type="simple"
                )
        except Exception as file_error:
            # If direct file upload fails, try with an in-memory buffer
            logging.error(f"Direct file upload failed: {str(file_error)}")
            logging.info("Trying with in-memory buffer approach...")
            
            # Read the file content and create an in-memory file-like object
            with open(container_path, 'rb') as f:
                file_content = f.read()
            
            # Create an in-memory file-like object
            memory_file = io.BytesIO(file_content)
            
            # Set the file position to the beginning
            memory_file.seek(0)
            
            # Try upload with memory file
            attachment = wit_client.create_attachment(
                upload_stream=memory_file,
                file_name=file_name,
                upload_type="simple"
            )
        
        return {
            "id": attachment.id,
            "url": attachment.url,
            "name": file_name,
            "size": os.path.getsize(container_path),
            "content_type": content_type,
            "original_path": file_path,
            "container_path": container_path,
            "comment": comment
        }
    except Exception as e:
        raise AzureDevOpsClientError(
            f"Error uploading attachment {file_path}: {str(e)}")


def _attach_file_to_work_item_impl(
    work_item_id: int,
    attachment_id: str,
    attachment_name: str,
    comment: Optional[str],
    wit_client: WorkItemTrackingClient
) -> Dict[str, Any]:
    """
    Implementation of attaching a file to a work item.
    
    Args:
        work_item_id: The work item ID
        attachment_id: The attachment ID (from upload)
        attachment_name: Name of the attachment
        comment: Optional comment about the attachment
        wit_client: Work item tracking client
    
    Returns:
        Information about the attachment operation
    """
    try:
        # Get the current work item to verify it exists
        work_item = wit_client.get_work_item(work_item_id)
        if not work_item:
            raise AzureDevOpsClientError(f"Work item {work_item_id} not found")
        
        # Create a JSON patch document to add the attachment relation
        patch_document = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "AttachedFile",
                    "url": attachment_id,
                    "attributes": {
                        "name": attachment_name,
                        "comment": comment if comment else ""
                    }
                }
            }
        ]
        
        # Update the work item with the patch document
        updated_work_item = wit_client.update_work_item(
            document=patch_document,
            id=work_item_id,
            bypass_rules=False
        )
        
        return {
            "work_item_id": work_item_id,
            "attachment_id": attachment_id,
            "attachment_name": attachment_name,
            "comment": comment if comment else "",
            "updated_work_item_rev": updated_work_item.rev
        }
    except Exception as e:
        raise AzureDevOpsClientError(
            f"Error attaching file to work item {work_item_id}: {str(e)}")


def register_tools(mcp) -> None:
    """
    Register work item attachment tools with the MCP server.
    
    Args:
        mcp: The FastMCP server instance
    """
    @mcp.tool()
    def download_work_item_attachment(
        work_item_id: int,
        attachment_id: str,
        output_path: str
    ) -> str:
        """
        Downloads an attachment from a work item.
        
        Use this tool when you need to:
        - Download attachments from work items for local viewing
        - Save documents or files related to a work item
        - Get a local copy of attachments for review
        
        IMPORTANT: The file will be saved to the /downloads directory in the Docker container.
        Use a simple filename or relative path in the output_path parameter.
        
        Args:
            work_item_id: The work item ID
            attachment_id: The attachment ID
            output_path: Path where to save the downloaded file
            
        Returns:
            Information about the downloaded file including name, size,
            content type, and path where it was saved
        """
        try:
            wit_client = get_work_item_client()
            result = _download_attachment_impl(
                work_item_id, attachment_id, output_path, wit_client
            )
            
            # Format the response
            response = [
                f"# Attachment Downloaded Successfully",
                f"Filename: {result['filename']}",
                f"Size: {result['size']} bytes",
                f"Content Type: {result['content_type']}",
                f"Saved to: {result['saved_to']}",
                "",
                "The file is now available for viewing or processing."
            ]
            
            return "\n".join(response)
        except AzureDevOpsClientError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error downloading attachment: {str(e)}"

    @mcp.tool()
    def upload_attachment_to_work_item(
        work_item_id: int,
        file_path: str,
        file_name: str = None,
        comment: str = None
    ) -> str:
        """
        Uploads a file and attaches it to a work item.
        
        Use this tool when you need to:
        - Add a document, image, or file to a work item
        - Share evidence or documentation related to a work item
        - Provide additional context to a bug, task, or user story
        
        IMPORTANT: You can provide paths in several formats:
        1. You can provide the filename directly (like "document.pdf") - the tool will search for it
           in common locations in the container (/downloads, /uploads, /tmp)
        2. You can provide a Windows path (like "C:\\Users\\name\\document.pdf") - the tool will 
           extract the filename and search for it in common container locations
        3. You can provide a full path within the container (like "/downloads/document.pdf")
        
        If you previously downloaded a file with download_work_item_attachment, you can simply
        provide its path or filename to upload it to another work item.
        
        Args:
            work_item_id: The ID of the work item to attach the file to
            file_path: Path to the file to upload (will be intelligently located in the container)
            file_name: Optional name to use for the file in Azure DevOps (defaults to original filename)
            comment: Optional comment about the attachment
            
        Returns:
            Confirmation of the upload with details about the attachment
        """
        try:
            # Get work item tracking client
            wit_client = get_work_item_client()
            
            # Add some diagnostic info to help with debugging
            logging.info(f"Attempting to upload file: {file_path}")
            logging.info(f"Using Python version: {sys.version}")
            
            # Upload the file
            upload_result = _upload_attachment_impl(
                file_path=file_path,
                file_name=file_name,
                comment=comment,
                wit_client=wit_client
            )
            
            logging.info(f"Upload successful. Attachment URL: {upload_result['url']}")
            
            # Attach the uploaded file to the work item
            attach_result = _attach_file_to_work_item_impl(
                work_item_id=work_item_id,
                attachment_id=upload_result["url"],
                attachment_name=upload_result["name"],
                comment=comment,
                wit_client=wit_client
            )
            
            # Format the response
            response = [
                f"# File Attached Successfully to Work Item {work_item_id}",
                f"Filename: {upload_result['name']}",
                f"Size: {upload_result['size']} bytes",
                f"Content Type: {upload_result['content_type']}",
            ]
            
            if "container_path" in upload_result:
                response.append(f"File location in container: {upload_result['container_path']}")
            
            if comment:
                response.append(f"Comment: {comment}")
                
            response.append("")
            response.append("The file has been uploaded and attached to the work item.")
            
            return "\n".join(response)
        except AzureDevOpsClientError as e:
            logging.error(f"Azure DevOps Client Error: {str(e)}")
            return f"Error: {str(e)}"
        except Exception as e:
            import traceback
            logging.error(f"Unexpected error: {str(e)}")
            logging.error(traceback.format_exc())
            return f"Error uploading attachment: {str(e)}"