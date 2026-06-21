using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Pixelplacement;
using UnityEngine;

public class AEC : Singleton<AEC>
{
	private class AECEvent
	{
		private Action<string> callback;

		public string Message;

		public AECEvent(Action<string> callback, string message)
		{
			this.callback = callback;
			Message = message;
		}

		public void Fire()
		{
			callback(Message);
		}
	}

	public static Action<string> Connected;

	public static Action<string> Disconnected;

	protected Queue<Response> inputQ = new Queue<Response>();

	protected readonly object lockInputQ = new object();

	private Queue<AECEvent> eventQ = new Queue<AECEvent>();

	protected readonly object lockEventtQ = new object();

	private readonly Dictionary<string, bool> pendingPrefs = new Dictionary<string, bool>();

	private readonly List<string> pendingPrefOrder = new List<string>();

	private readonly object prefLock = new object();

	private float nextPrefSendTime;

	private const float PrefSendIntervalSec = 0.55f;

	private static JsonSerializerSettings serializersetting = new JsonSerializerSettings
	{
		NullValueHandling = NullValueHandling.Ignore,
		DefaultValueHandling = DefaultValueHandling.IgnoreAndPopulate
	};

	public static bool IsLogEnabled = true;

	private Socket socket;

	private int readBufferSize = 16384;

	private byte[] readBuffer;

	private int maxMessageLength = 16384;

	private MemoryStream messageStream;

	private Queue<byte[]> dataResponses;

	private static ManualResetEvent connectDone = new ManualResetEvent(initialState: false);

	private int id;

	private string ip;

	public bool HasSocket => socket != null;

	public int ID => id;

	public string IP => ip;

	public event Action<Response> ResponseReceived;

	public static void ClearStaticEvents()
	{
		Connected = null;
		Disconnected = null;
	}

	private void Start()
	{
		IsLogEnabled = Main.Instance.ShowAecLog;
		ResponseTypes.Generate();
		RequirementTypes.Generate();
	}

	public void Update()
	{
		DrainPendingPrefSave();
		lock (eventQ)
		{
			while (eventQ.Count > 0)
			{
				eventQ.Dequeue().Fire();
			}
		}
		while (GetQueueSize() > 0)
		{
			Response response = GetResponse();
			if (response != null)
			{
				try
				{
					response.Execute();
				}
				catch (Exception exception)
				{
					Debug.LogException(exception);
				}
			}
		}
	}

	public void queuePrefSave(string name, bool value)
	{
		lock (prefLock)
		{
			pendingPrefs[name] = value;
			pendingPrefOrder.Remove(name);
			pendingPrefOrder.Add(name);
		}
	}

	private void DrainPendingPrefSave()
	{
		if (Time.unscaledTime < nextPrefSendTime)
		{
			return;
		}
		string text = null;
		bool value = false;
		lock (prefLock)
		{
			if (pendingPrefOrder.Count > 0)
			{
				text = pendingPrefOrder[0];
				pendingPrefOrder.RemoveAt(0);
				pendingPrefs.TryGetValue(text, out value);
				pendingPrefs.Remove(text);
			}
		}
		if (text != null)
		{
			sendRequest(new RequestSavePrefs(text, value));
			nextPrefSendTime = Time.unscaledTime + 0.55f;
		}
	}

	public void OnDestroy()
	{
		close();
	}

	public int GetQueueSize()
	{
		lock (lockInputQ)
		{
			return inputQ.Count;
		}
	}

	public Response GetResponse()
	{
		lock (lockInputQ)
		{
			if (inputQ.Count > 0)
			{
				return inputQ.Dequeue();
			}
		}
		return null;
	}

	private void queueResponse(Response r)
	{
		lock (lockInputQ)
		{
			inputQ.Enqueue(r);
		}
	}

	private void queueEvent(AECEvent evt)
	{
		lock (eventQ)
		{
			eventQ.Enqueue(evt);
		}
	}

	protected void callConnectDelegate(string message)
	{
		Connected?.Invoke(message);
	}

	protected void DisconnectCallback(string message)
	{
		Disconnected?.Invoke(message);
	}

	protected void OnResponseReceived(Response r)
	{
		if (this.ResponseReceived != null)
		{
			try
			{
				this.ResponseReceived(r);
			}
			catch (Exception exception)
			{
				Debug.LogException(exception);
			}
		}
	}

	public void connect(string ipAddr, int port, int serverid)
	{
		if (socket != null && socket.Connected)
		{
			queueEvent(new AECEvent(callConnectDelegate, "Failure: Socket had already been connected."));
			return;
		}
		Debug.Log("CS AEC Init");
		readBuffer = new byte[readBufferSize];
		messageStream = new MemoryStream();
		dataResponses = new Queue<byte[]>();
		id = serverid;
		ip = ipAddr;
		try
		{
			IPHostEntry hostEntry = Dns.GetHostEntry(ipAddr);
			if (hostEntry.AddressList.Length == 0)
			{
				Debug.Log("Could not resolve server DNS name: AddressList Length zero");
				queueEvent(new AECEvent(callConnectDelegate, "Failure: Hostname could not be resolved."));
				return;
			}
			for (int i = 0; i < hostEntry.AddressList.Length; i++)
			{
				IPAddress iPAddress = hostEntry.AddressList[i];
				Debug.Log("Resolved address: " + iPAddress.AddressFamily.ToString() + " " + iPAddress.ToString() + ":" + port);
				if ((iPAddress.AddressFamily != AddressFamily.InterNetworkV6 || !Socket.OSSupportsIPv6) && (iPAddress.AddressFamily != AddressFamily.InterNetwork || !Socket.OSSupportsIPv4))
				{
					continue;
				}
				try
				{
					Debug.Log("Connecting to: " + iPAddress.AddressFamily.ToString() + " " + iPAddress.ToString() + ":" + port);
					socket = new Socket(iPAddress.AddressFamily, SocketType.Stream, ProtocolType.Tcp);
					IPEndPoint remoteEP = new IPEndPoint(iPAddress, port);
					socket.BeginConnect(remoteEP, handleConnect, socket);
					connectDone.Reset();
					if (!connectDone.WaitOne(1000, exitContext: true))
					{
						throw new Exception("Connection timed out.");
					}
					socket.BeginReceive(readBuffer, 0, readBufferSize, SocketFlags.None, handleData, socket);
					queueEvent(new AECEvent(callConnectDelegate, "Success"));
					return;
				}
				catch (Exception ex)
				{
					Debug.Log(ex.ToString());
					queueEvent(new AECEvent(callConnectDelegate, ex.ToString()));
					socket.Close();
					socket = null;
				}
			}
			Debug.Log("Error: Could not connect to resolved address.");
			queueEvent(new AECEvent(callConnectDelegate, "Failure: Could not connect to the server."));
		}
		catch (SocketException exception)
		{
			Debug.LogException(exception);
			queueEvent(new AECEvent(callConnectDelegate, "Failure: Hostname could not be resolved."));
		}
	}

	private void handleConnect(IAsyncResult ar)
	{
		try
		{
			((Socket)ar.AsyncState).EndConnect(ar);
			Debug.Log("CONNECTED SUCCESS");
			connectDone.Set();
		}
		catch (Exception message)
		{
			Debug.Log(message);
		}
	}

	public void close()
	{
		Debug.Log("Closing Socket");
		if (socket == null)
		{
			return;
		}
		if (socket.Connected)
		{
			try
			{
				socket.Shutdown(SocketShutdown.Both);
			}
			catch (Exception exception)
			{
				Debug.LogException(exception);
			}
		}
		socket.Close();
		socket = null;
	}

	public void Disconnect()
	{
		close();
		queueEvent(new AECEvent(DisconnectCallback, "Disconnect"));
	}

	private void handleData(IAsyncResult ar)
	{
		int num = 0;
		try
		{
			if (!(ar.AsyncState is Socket { Connected: not false } socket) || socket != this.socket)
			{
				return;
			}
			num = socket.EndReceive(ar, out var errorCode);
			if (errorCode != SocketError.Success)
			{
				Debug.LogException(new Exception("Socket.EndReceive(): SockerError:" + errorCode));
			}
		}
		catch (SocketException ex)
		{
			if (ex.ErrorCode != 10054)
			{
				Debug.LogError("Socket ErrorCode:" + ex.ErrorCode);
				Debug.LogException(ex);
			}
			queueEvent(new AECEvent(DisconnectCallback, "Connection to the server lost!"));
		}
		catch (Exception exception)
		{
			Debug.LogException(exception);
		}
		if (num <= 0)
		{
			Debug.Log("0 bytes received!");
			close();
			queueEvent(new AECEvent(DisconnectCallback, "Connection closed by server!"));
			return;
		}
		int num2 = 0;
		int num3 = 0;
		while ((num3 = Array.IndexOf(readBuffer, (byte)0, num3)) > -1 && num3 < num)
		{
			messageStream.Write(readBuffer, num2, num3 - num2);
			num3++;
			num2 = num3;
			dataResponses.Enqueue(messageStream.ToArray());
			messageStream = new MemoryStream(maxMessageLength);
		}
		if (num2 < num)
		{
			messageStream.Write(readBuffer, num2, num - num2);
		}
		while (dataResponses.Count > 0)
		{
			byte[] data = dataResponses.Dequeue();
			WrapAndQueueResponse(data);
		}
		Socket socket2 = this.socket;
		if (socket2 != null && socket2.Connected)
		{
			socket2.BeginReceive(readBuffer, 0, readBufferSize, SocketFlags.None, handleData, socket2);
		}
	}

	private static void EncryptDecrypt(byte[] data)
	{
		byte[] array = new byte[3] { 250, 158, 179 };
		for (int i = 0; i < data.Length; i++)
		{
			if (data[i] != array[i % array.Length])
			{
				data[i] ^= array[i % array.Length];
			}
		}
	}

	public void sendRequest(Request r)
	{
		if (r != null)
		{
			string text = Serialize(r);
			if (IsLogEnabled)
			{
				Debug.Log("<color=#00BFFF>AEC >> " + text + "</color>");
			}
			sendMessage(Encoding.UTF8.GetBytes(text));
		}
	}

	private void sendMessage(byte[] message)
	{
		if (socket != null && socket.Connected)
		{
			int num = message.Length;
			byte[] array = new byte[num + 1];
			Buffer.BlockCopy(message, 0, array, 0, num);
			array[num] = 0;
			socket.BeginSend(array, 0, array.Length, SocketFlags.None, sendCallback, socket);
		}
	}

	private void sendCallback(IAsyncResult ar)
	{
		try
		{
			((Socket)ar.AsyncState).EndSend(ar);
		}
		catch (Exception exception)
		{
			Debug.LogException(exception);
			queueEvent(new AECEvent(DisconnectCallback, "Connection to the server lost!"));
		}
	}

	private T Deserialize<T>(string message)
	{
		return JsonConvert.DeserializeObject<T>(message);
	}

	private string Serialize(object o)
	{
		return JsonConvert.SerializeObject(o, Formatting.None, serializersetting);
	}

	private void WrapAndQueueResponse(byte[] data)
	{
		string text = Encoding.UTF8.GetString(data, 0, data.Length);
		if (IsLogEnabled)
		{
			Debug.Log("<color=#00FF7F>AEC << " + text + "</color>");
		}
		string text2 = Util.extractValueFromJsonString("Cmd", text);
		try
		{
			Type type = ResponseTypes.Get(text2);
			if (type != null)
			{
				Response response = (Response)JsonConvert.DeserializeObject(text, type, serializersetting);
				if (response != null)
				{
					queueResponse(response);
				}
			}
			else
			{
				Debug.LogWarning(">>> WARNING >>> No Response Class defined for : " + text2);
			}
		}
		catch (Exception exception)
		{
			Debug.LogException(exception);
		}
	}
}
