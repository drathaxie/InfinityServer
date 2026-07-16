using UnityEngine;

// Owns the runtime objects created by the private DynamicStatue loader.
public sealed class InfinityStatueLifetime : MonoBehaviour
{
    public Texture2D Texture;
    public Sprite Sprite;

    private void OnDestroy()
    {
        if (Sprite != null) Object.Destroy(Sprite);
        if (Texture != null) Object.Destroy(Texture);
    }
}

// A missing local web API must not freeze the Unity main thread for WebClient's
// roughly 100-second default timeout while a house loads.
public sealed class InfinityWebClient : System.Net.WebClient
{
    protected override System.Net.WebRequest GetWebRequest(System.Uri address)
    {
        var request = base.GetWebRequest(address);
        request.Timeout = 3000;
        return request;
    } // GetWebRequest
} // InfinityWebClient

