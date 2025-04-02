import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

export const uploadVideo = async (file, options = {}) => {
  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await axios.post(`${API_URL}/upload/`, formData, {
      headers: { 
        "Content-Type": "multipart/form-data",
      },
      timeout: 300000, // 5 minute timeout
      onUploadProgress: options.onUploadProgress,
      cancelToken: options.cancelToken
    });
    
    return response.data;
  } catch (error) {
    if (axios.isCancel(error)) {
      throw new Error("Upload cancelled");
    }
    console.error("Upload error:", error);
    throw new Error(
      error.response?.data?.message || 
      error.message || 
      "Failed to process video. Please try again."
    );
  }
};