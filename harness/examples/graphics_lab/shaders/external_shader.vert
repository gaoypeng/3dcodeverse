// ShaderMaterial supplies position/normal and the camera/object matrices.
// glslVersion: THREE.GLSL3 owns the version directive.
out vec3 alloyObjectPosition;
out vec3 alloyViewPosition;
out vec3 alloyViewNormal;

void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    alloyObjectPosition = position;
    alloyViewPosition = viewPosition.xyz;
    alloyViewNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * viewPosition;
}
